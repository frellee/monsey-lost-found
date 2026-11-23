"""Yiddish/English mixed-voicemail bridge for the Intermedia listener.

The listener uses faster-whisper for ordinary English. This helper runs the locally
installed Yiddish-fine-tuned ONNX model through its *separate* Python environment
only when ordinary Whisper's language detection suggests Yiddish.

No network calls, passwords, or transcript uploads.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata

LOG = logging.getLogger(__name__)
MODEL_ROOT = Path.home() / "Downloads" / "YiddishModel"
MODEL_PYTHON = MODEL_ROOT / "yiddish_test_env" / "Scripts" / "python.exe"

# Transcribe in Yiddish script, and only convert *recognizable phonetic English*
# product names/words back to Latin letters. Native Yiddish words stay Yiddish:
# צירונגען does not become jewelry; הויזן does not become pants.
# These are spelling fixes, not guesses about which items were lost.
ENGLISH_SPELLINGS = {
    "iPad": (
        "אייפאד", "אייפעד", "אייפעט", "אייפד", "איי פאד", "איי פעד",
        "אײַפּאַד", "אייפאָד", "אייפּאַד", "אייפּעד",
    ),
    "AirPods": (
        "איירפאדס", "איירפודס", "אירפאדס", "אערפאדס",
        "ערפאדס", "אירפודס", "עירפאדס", "אייר פאדס", "איירפּאָדס",
    ),
    "AirPod": ("איירפאד", "איירפוד", "אירפאד", "איירפּאָד"),
    "iPhone": ("אייפון", "אייפאון", "אייפאָון", "איי פאון"),
    "jewelry": (
        "דזשולערי", "דזשולרי", "דזשועלרי", "דזשועולרי", "דזשעוועלרי",
        "דזשוועלרי", "דזשולעריע", "דזשולארי", "דזולערי",
    ),
    "plastic": ("פלאסטיק", "פּלאַסטיק", "פּלסטיק"),
    "cover": ("קאווער", "קאָווער", "קאָווֶר", "קאבער"),
    "charger": ("טשארדזשער", "טשאַרדזשער", "טשארגער"),
    "laptop": ("לעפטאפ", "לאפטאפ", "לעפטאָפּ", "לאַפּטאַפּ"),
    "backpack": ("בעקפעק", "בעקפּעק", "בעקפאק"),
    "wallet": ("וואלעט", "וואָלעט"),
    "phone case": ("פאון קעיס", "פאָן קייס", "פון קעיס"),
    "power bank": ("פאוער בענק", "פּאַוער בענק"),
}

YIDDISH_MARKERS = (
    "איך", "האב", "האָב", "מיין", "מײַן", "פארלוירן", "פֿאַרלוירן",
    "מיך", "מיר", "אויף", "אויפן", "אויפֿן", "געווען", "מענטש", "זאכן",
    "זאַכן", "דער", "דאס", "דאָס", "ביטע", "רופט", "רופֿט", "געפינען", "געפֿינען",
)


def _normalize_word(value: str) -> str:
    v = unicodedata.normalize("NFKD", value)
    # Remove Yiddish vowel-point differences for comparisons only.
    v = "".join(ch for ch in v if unicodedata.category(ch) != "Mn")
    v = v.replace("װ", "וו").replace("ײ", "יי").replace("ױ", "וי")
    return re.sub(r"\s+", " ", v.strip())


# Map normalized strings, not raw dictionary spellings. This fixes the bug in
# earlier versions where accent-marked Yiddish words would never match.
_NORMALIZED_ENGLISH = {
    _normalize_word(pronunciation): english
    for english, pronunciations in ENGLISH_SPELLINGS.items()
    for pronunciation in pronunciations
}


def format_code_switching(text: str) -> str:
    """Preserve Yiddish sentences and restore known spoken English expressions.

    Never translate Yiddish words or insert items not present in the text.
    For unknown words, preserve the model's original output for verification.
    """
    # Longest phrases first, including two-word English phrases.
    result = text
    for source, english in sorted(_NORMALIZED_ENGLISH.items(), key=lambda pair: len(pair[0]), reverse=True):
        # Spellings with niqqud are compared using a separate normalized-text
        # form only when the whole text contains Hebrew vowel marks.
        escaped = re.escape(source).replace(r"\ ", r"\s+")
        pattern = rf"(?<![\u0590-\u05FF]){escaped}(?![\u0590-\u05FF])"
        if re.search(pattern, result):
            result = re.sub(pattern, lambda _m: english, result)
    # For single words with Yiddish vowel marks, do a token-level lookup.
    def convert_word(match: re.Match[str]) -> str:
        word = match.group(0)
        return _NORMALIZED_ENGLISH.get(_normalize_word(word), word)

    result = re.sub(r"[\u0590-\u05FF]+", convert_word, result)
    # Already-Latin forms: fix just established product spellings.
    result = re.sub(r"(?i)(?<![a-z])i[ -]?pad(?![a-z])", "iPad", result)
    result = re.sub(r"(?i)(?<![a-z])air[ -]?pods(?![a-z])", "AirPods", result)
    result = re.sub(r"(?i)(?<![a-z])i[ -]?phone(?![a-z])", "iPhone", result)
    return result


def _plausibly_yiddish(text: str) -> bool:
    letters = re.findall(r"[\u05D0-\u05EA]", text)
    if len(letters) < 18:
        return False
    words = set(_normalize_word(w) for w in re.findall(r"[\u0590-\u05FF]+", text))
    markers = set(_normalize_word(w) for w in YIDDISH_MARKERS)
    return len(words & markers) >= 2


def transcribe_en_yiddish(audio_path: Path, whisper_model, vocabulary_hint: str = "") -> tuple[str, str, str]:
    """Return (display transcript, initial raw transcript, displayed language).

    English remains in the reliable original transcription engine. For calls
    likely spoken in Yiddish, use the independently installed Yiddish model.
    Fall back safely if the Yiddish model is unavailable or unconvincing.
    """
    segments, info = whisper_model.transcribe(
        str(audio_path), beam_size=5, vad_filter=True, task="transcribe",
        initial_prompt=vocabulary_hint, condition_on_previous_text=False,
    )
    raw = " ".join(seg.text.strip() for seg in segments).strip()
    lang = getattr(info, "language", "unknown") or "unknown"
    confidence = float(getattr(info, "language_probability", 1.0) or 0.0)
    from_source = raw

    # The older Whisper often calls Yiddish Dutch, German or Russian.
    # Only examine low-confidence English; ordinary English skips the slow model.
    should_try_yiddish = (lang in {"nl", "ru", "de", "yi", "he", "pl", "uk"}
                          or (lang == "en" and confidence < 0.55))
    # --- BEGIN OPTIONAL PHONETIC YIDDISH DETECTION V1 ---
    # The default Whisper can call Yiddish "English". Try the Yiddish model
    # only for English text with multiple unmistakably Yiddish phonetic cues.
    if not should_try_yiddish and lang == "en":
        try:
            from heimish_mixed_language import looks_like_yiddish_in_english
            should_try_yiddish = looks_like_yiddish_in_english(raw)
        except Exception:
            pass
    # --- END OPTIONAL PHONETIC YIDDISH DETECTION V1 ---
    if not should_try_yiddish:
        return _common_english_spelling(from_source), raw, lang
    if not MODEL_PYTHON.is_file():
        LOG.warning("Yiddish Python environment missing; using ordinary transcription.")
        return _common_english_spelling(from_source), raw, lang

    with tempfile.TemporaryDirectory(prefix="yiddish_result_") as tmp:
        output = Path(tmp) / "result.json"
        cmd = [str(MODEL_PYTHON), str(Path(__file__).resolve()),
               "--worker", str(audio_path), str(output)]
        try:
            env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=900,
                                  env=env, check=False)
            if proc.returncode != 0 or not output.is_file():
                LOG.warning("Yiddish model failed; preserving original: %s",
                            (proc.stderr or proc.stdout)[-400:])
                return _common_english_spelling(from_source), raw, lang
            yiddish_raw = json.loads(output.read_text(encoding="utf-8"))["transcript"].strip()
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as ex:
            LOG.warning("Yiddish model unavailable; preserving original: %s", ex)
            return _common_english_spelling(from_source), raw, lang

    if not _plausibly_yiddish(yiddish_raw):
        LOG.info("Yiddish candidate not convincing; using ordinary transcript.")
        return _common_english_spelling(from_source), raw, lang

    formatted = format_code_switching(yiddish_raw)
    LOG.info("Used local Yiddish model; English product spellings kept in Latin script.")
    return formatted, yiddish_raw, "yi"


def _common_english_spelling(text: str) -> str:
    # Avoid rewording names, phone numbers and uncertain Yiddish.
    replacements = (
        (r"(?i)\bgear[ -]?pods\b", "AirPods"),
        (r"(?i)\bair[ -]?pods\b", "AirPods"),
        (r"(?i)\bair[ -]?pod\b", "AirPod"),
        (r"(?i)\bear[ -]?pods\b", "AirPods"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    return text



# MONSEY_HEIMISH_SPELLING_V1
# Normalize only highly recognizable *item-name pronunciations* in English.
# Keep the original audio-derived transcript for verification; never rewrite
# ambiguous everyday phrases (e.g. "my arm") into religious articles.
_heimish_prior_common_spelling = _common_english_spelling

def _common_english_spelling(text: str) -> str:
    text = _heimish_prior_common_spelling(text)
    variants = (
        (r'(?i)\b(?:talis|tallis|tallit|tallith|talith)\b', 'Tallis'),
        (r'(?i)\b(?:tefillin|tefilin|tfillin|tefillen|tfilin|tfilen)\b', 'Tefillin'),
        (r'(?i)\b(?:sidur|siddur|siddor|siddar)\b', 'Siddur'),
        (r'(?i)\b(?:tehillim|tehilim|tehilim)\b', 'Tehillim'),
        (r'(?i)\b(?:chumash|humash|khumash)\b', 'Chumash'),
    )
    for pattern, label in variants:
        text = re.sub(pattern, label, text)
    return text

def _model_worker(audio_file: str, result_file: str) -> None:
    """This part runs in the existing Yiddish *test* Python environment."""
    from transformers import AutoProcessor, GenerationConfig
    from optimum.onnxruntime import ORTModelForSpeechSeq2Seq
    import librosa

    root = MODEL_ROOT
    data = json.loads((root / "generation_config.json").read_text(encoding="utf-8-sig"))
    if "<|yi|>" not in data.get("lang_to_id", {}):
        raise RuntimeError("Yiddish language token is missing")

    onnx = root / "onnx"
    onnx.mkdir(exist_ok=True)
    for pattern, name in (("encoder_model_q4*.onnx", "encoder_model.onnx"),
                          ("decoder_model_merged_q4*.onnx", "decoder_model_merged.onnx")):
        candidates = sorted(root.glob(pattern))
        if not candidates:
            raise FileNotFoundError("Missing " + pattern + " in " + str(root))
        source = max(candidates, key=lambda x: x.stat().st_size)
        target = onnx / name
        if not target.is_file() or target.stat().st_size != source.stat().st_size:
            shutil.copy2(source, target)

    processor = AutoProcessor.from_pretrained(str(root), local_files_only=True)
    model = ORTModelForSpeechSeq2Seq.from_pretrained(
        str(root), subfolder="onnx", local_files_only=True, use_cache=False)
    generation = GenerationConfig.from_pretrained(str(root), local_files_only=True)
    generation.lang_to_id = data["lang_to_id"]
    generation.task_to_id = data["task_to_id"]
    generation.is_multilingual = True
    generation.forced_decoder_ids = None
    model.generation_config = generation

    audio, _ = librosa.load(audio_file, sr=16000, mono=True)
    inputs = processor(audio, sampling_rate=16000, return_tensors="pt")
    tokens = model.generate(**inputs, generation_config=generation,
                            language="yi", task="transcribe", num_beams=4,
                            do_sample=False, max_new_tokens=192)
    transcript = processor.batch_decode(tokens, skip_special_tokens=True)[0].strip()
    Path(result_file).write_text(json.dumps({"transcript": transcript}, ensure_ascii=False),
                                encoding="utf-8")


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == "--worker":
    _model_worker(sys.argv[2], sys.argv[3])

# --- BEGIN HEIMISH MIXED YIDDISH V1 ---
# Wrap only the final displayed Yiddish spelling. Existing speech recognition
# and the original English voicemail path are preserved.
from heimish_mixed_language import format_heimish_mixed as _heimish_format
_original_heimish_formatter = format_code_switching

def format_code_switching(text: str) -> str:
    return _heimish_format(_original_heimish_formatter(text))
# --- END HEIMISH MIXED YIDDISH V1 ---
