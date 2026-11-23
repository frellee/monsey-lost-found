"""Local Yiddish + English orthography layer for voicemail transcription.

Only known transliterations of spoken English words are restored to Latin script.
This is NOT speech recognition or translation: it never invents a new item.
Standard library only. Copyright 2026, freely reusable under MIT terms.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
import unicodedata

DATA_FILE = Path(__file__).resolve().with_name('heimish_english_words.json')
HEBREW_TOKEN = re.compile(r'^[\u0590-\u05ff]+$')
TOKENIZER = re.compile(r'[\u0590-\u05ff]+|\s+|[^\u0590-\u05ff\s]+')


def _normalized(text: str) -> str:
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(char for char in text if unicodedata.category(char) != 'Mn')
    return text.replace('װ', 'וו').replace('ײ', 'יי').replace('ױ', 'וי').strip()


def _index() -> dict[str, str]:
    entries = json.loads(DATA_FILE.read_text(encoding='utf-8'))['entries']
    lookup = {}
    for item in entries:
        for spelling in item['heard_as']:
            key = _normalized(spelling)
            if key in lookup and lookup[key] != item['english']:
                raise ValueError('Conflicting spelling mapping: ' + key)
            lookup[key] = item['english']
    return lookup


def format_heimish_mixed(text: str) -> str:
    """Preserve native Yiddish, rewrite only listed phonetic English words.

    A multiword loanword is recognized only when the EXACT normalized
    Yiddish sequence occurs. Common Yiddish words are never translated.
    """
    if not text or not isinstance(text, str):
        return text
    lookup = _index()
    parts = TOKENIZER.findall(text)
    out = []
    i = 0
    while i < len(parts):
        token = parts[i]
        if not HEBREW_TOKEN.fullmatch(token):
            out.append(token)
            i += 1
            continue
        replacement = None
        consumed = 1
        # Test up to 3 Hebrew words, longest first (allow spaces, not punctuation).
        for count in (3, 2, 1):
            end = i + count * 2 - 1
            if end > len(parts):
                continue
            sample = parts[i:end]
            if (all(HEBREW_TOKEN.fullmatch(x) for x in sample[0::2]) and
                all(x.isspace() for x in sample[1::2])):
                candidate = ' '.join(_normalized(x) for x in sample[0::2])
                if candidate in lookup:
                    replacement = lookup[candidate]
                    consumed = len(sample)
                    break
        if replacement is not None:
            out.append(replacement)
            i += consumed
        else:
            out.append(token)
            i += 1
    result = ''.join(out)
    # Correct case ONLY for already-recognized, unambiguous Latin names.
    for variant, preferred in (
        (r'(?i)(?<![a-z])i[ -]?pad(?![a-z])', 'iPad'),
        (r'(?i)(?<![a-z])air[ -]?pods(?![a-z])', 'AirPods'),
        (r'(?i)(?<![a-z])i[ -]?phone(?![a-z])', 'iPhone'),
    ):
        result = re.sub(variant, preferred, result)
    return result


def looks_like_yiddish_in_english(text: str) -> bool:
    """Catch some *obviously* Yiddish calls mislabeled high-confidence English.

    Requires multiple phonetic Yiddish cues to avoid slowing down normal calls.
    This is conservative and cannot identify every heimishe accent.
    """
    text = text.lower()
    patterns = (
        r'\b(?:ich|ikh|ech|ick)\s+(?:hob|hab|heb|hot|hobn)\b',
        r'\b(?:farlorn|farloiren|verloiren|farloyn|farloynen|farlohren|farlore|ferloiren|verloren)\b',
        r'\b(?:afn|oyfn|oifen|oyf|biz|azoy|asoy|shoyn|shoin|shun)\b',
        r'\b(?:zakh|zakhn|sakh|sachen|gezogt|gehat|gevayn|geven|geveyn)\b',
        r'\b(?:vos|voos|kumen|gekumen|gufunen|gefinen|gefunen)\b',
    )
    hits = sum(bool(re.search(p, text)) for p in patterns)
    return hits >= 2
