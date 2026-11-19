"""Gemini voicemail transcription + Yiddish->English translation.

Sends ONE voicemail audio file to Google Gemini over HTTPS and gets back the words
that were spoken plus an English translation. The API key is read ONLY from the
environment variable MONSEY_GEMINI_API_KEY (loaded from your private file by
START_ALL_SAFE.bat). The key is never written to any file or log.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MODEL = os.environ.get('MONSEY_GEMINI_AUDIO_MODEL', 'gemini-3.8-flash').strip() or 'gemini-3.8-flash'
FALLBACK_MODEL = 'gemini-3.5-flash' if MODEL == 'gemini-3.8-flash' else 'gemini-3.8-flash'
TIMEOUT_SECONDS = 180
MAX_AUDIO_BYTES = 15 * 1024 * 1024
MIME = {'.mp3': 'audio/mp3', '.wav': 'audio/wav', '.m4a': 'audio/mp4',
        '.ogg': 'audio/ogg', '.webm': 'audio/webm'}


class GeminiVoicemailError(RuntimeError):
    pass


# The listener reads this right after a transcription to save the item list.
LAST_RESULT: dict = {}


PROMPT = """You transcribe voicemails left for a bus company's lost-and-found office.
Callers speak English, Yiddish (including Hasidic / Heimish Yiddish), or a mix of both.
Listen carefully and return ONLY a JSON object, no code fences:
{
  "language": "en" or "yi" or "mixed" or "other",
  "original_text": "exactly what was said, in the language it was said",
  "english_text": "faithful English translation of the whole message (same as original_text if it is already English)",
  "callback_number": "digits of a callback number ONLY if clearly spoken, otherwise empty string",
  "items": [{"item": "short English item name", "color": "color the caller said for that item, or empty"}],
  "trip": {"bus_number": "bus number ONLY if the caller said it, else empty", "time": "time the caller rode the bus, as said (e.g. 3:30 PM, morning), else empty", "from": "place the bus left from, else empty", "to": "place the bus went to, else empty"},
  "unclear": ["words or numbers you could not hear clearly"]
}
Rules:
- Write Yiddish in Yiddish (Hebrew-letter) spelling. Write English words spoken inside a Yiddish
  sentence (iPad, AirPods, charger, backpack, wallet, phone case and similar) in English letters.
- Transcribe only what is actually said. Never invent, never fill in a guess.
- Never guess names, street names or phone digits. If you are not sure, write [unclear] in that spot
  and add it to the "unclear" list. A phone number must have exactly the digits you heard.
- Do not add items, places or details that were not spoken.
- If the audio is silent, only noise, or has no clear speech, return an empty original_text and english_text.
- Never repeat a word or phrase over and over. Write each spoken word once, as it was said.
- english_text must NEVER be empty when original_text has words in it.
- The callers are Hasidic Yiddish speakers (Monsey, Brooklyn). They usually say "איך האב פארלוירן ..." /
  "פארלוירן ..." (I lost ...) and then list items, often mixing English item names into Yiddish.
  Item words you may hear, ONLY if they are really said: טלית, תפילין, סידור, חומש, תהילים, שטריימל,
  היטל, קאפעלוש, זאקן (socks), הענטשקעס (gloves), רוקל, מאנטל, בעקפעק, וואלעט, שליסלען (keys),
  ברילן (glasses), טעלעפאן, אייפאד, איירפאדס, דרייידל, מנורה, ארבע כוסות.
  These are spelling help only. Never add one that was not spoken.
- Each item the caller names is a separate item. Do not drop items or merge them. Keep the order.
- "items" lists every lost item the caller mentions, ONCE each, in the order said. Use the common English name
  (a Jewish prayer book is Siddur; earbuds are AirPods only if AirPods are said). "color" is ONLY a color the
  caller actually said for THAT item, one simple lowercase word (red, black, blue, green, white, gray, brown,
  pink, yellow, orange, purple, gold, silver, beige, navy). If no color was said, use an empty string.
  Do not guess a color. If the caller repeats the same item, list it once.
- A phone number the caller gives so we can call them back is NOT a lost item. Only list a phone
  if the caller says they LOST a phone. "My phone number is ..." must never produce a "Phone" item.
- "trip" is about the bus ride where the item was lost. Fill a field ONLY if the caller actually said it
  (bus number, what time they rode, where from and where to). Leave a field empty if it was not said. Never guess.
- Useful context words: __HINT__
"""


def _read_key() -> str:
    key = os.environ.get('MONSEY_GEMINI_API_KEY', '').strip()
    if not key or key.lower().startswith(('paste ', 'put ', 'your ', '#', 'stored-in')):
        raise GeminiVoicemailError('Gemini key is not set. Start with START_ALL_SAFE.bat.')
    if not re.fullmatch(r'[A-Za-z0-9._\-]{16,256}', key):
        raise GeminiVoicemailError('The Gemini key in your private file looks invalid.')
    return key


def _extract_json(text: str) -> dict:
    text = (text or '').strip()
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
    try:
        value = json.loads(text)
    except ValueError:
        match = re.search(r'\{.*\}', text, re.S)
        if not match:
            raise GeminiVoicemailError('Gemini returned an unreadable answer.') from None
        try:
            value = json.loads(match.group(0))
        except ValueError:
            raise GeminiVoicemailError('Gemini returned an unreadable answer.') from None
    if not isinstance(value, dict):
        raise GeminiVoicemailError('Gemini returned an unexpected answer.')
    return value


def _thinking_level(model: str) -> str:
    forced = os.environ.get('MONSEY_GEMINI_AUDIO_THINKING', '').strip().lower()
    if forced in ('low', 'medium', 'high'):
        return forced
    return 'high' if ('pro' in model or 'argon' in model) else 'medium'


def _call(key: str, model: str, audio: bytes, mime: str, hint: str, use_thinking: bool = True) -> dict:
    url = 'https://generativelanguage.googleapis.com/v1beta/models/' + model + ':generateContent'
    body = {
        'contents': [{'role': 'user', 'parts': [
            {'text': PROMPT.replace('__HINT__', hint or 'AirPods, iPad, tefillin, tallis, Monsey')},
            {'inline_data': {'mime_type': mime, 'data': base64.b64encode(audio).decode('ascii')}},
        ]}],
        'generationConfig': {'responseMimeType': 'application/json', 'temperature': 0.0,
                             'maxOutputTokens': 16384},
    }
    if use_thinking:
        body['generationConfig']['thinkingConfig'] = {'thinkingLevel': _thinking_level(model)}
    req = Request(url, data=json.dumps(body).encode('utf-8'), method='POST',
                  headers={'Content-Type': 'application/json', 'x-goog-api-key': key})
    waits = (3, 8)
    for attempt in range(len(waits) + 1):
        try:
            with urlopen(req, timeout=TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read(2_000_000).decode('utf-8'))
            parts = (payload.get('candidates') or [{}])[0].get('content', {}).get('parts', [])
            text = ''.join(p.get('text', '') for p in parts if not p.get('thought'))
            if not text.strip():
                raise GeminiVoicemailError('Gemini returned no text (the message may have been blocked).')
            return _extract_json(text)
        except HTTPError as ex:
            if ex.code in (500, 502, 503, 504) and attempt < len(waits):
                time.sleep(waits[attempt])
                continue
            if ex.code == 404:
                raise GeminiVoicemailError(f'Model {model} not available (HTTP 404).') from None
            if ex.code == 400 and use_thinking:
                return _call(key, model, audio, mime, hint, use_thinking=False)
            if ex.code in (400, 401, 403):
                raise GeminiVoicemailError(f'Gemini HTTP {ex.code}: bad request, key or permission.') from None
            if ex.code == 429:
                raise GeminiVoicemailError('Gemini HTTP 429: limit reached. Check usage in AI Studio.') from None
            raise GeminiVoicemailError(f'Gemini HTTP {ex.code}.') from None
        except (URLError, TimeoutError, OSError):
            if attempt < len(waits):
                time.sleep(waits[attempt])
                continue
            raise GeminiVoicemailError('Could not reach Gemini (internet or timeout).') from None
    raise GeminiVoicemailError('Gemini did not respond.')


def _looping(text: str) -> bool:
    """True when the text looks like a model stuck repeating itself."""
    words = re.findall(r"\w+", text or "")
    if len(words) < 12:
        return False
    top = max(words.count(w) for w in set(words))
    return top >= 8 and top / len(words) >= 0.3 or len(set(words)) / len(words) < 0.2


def _translate_text(key: str, model: str, original: str) -> str:
    url = 'https://generativelanguage.googleapis.com/v1beta/models/' + model + ':generateContent'
    body = {
        'contents': [{'role': 'user', 'parts': [{'text':
            'Translate this voicemail transcript (Yiddish or English) into plain English. '
            'Do not add anything that is not in it. Keep [unclear] markers. '
            'Return ONLY JSON: {"english_text": "..."}\n\n' + original}]}],
        'generationConfig': {'responseMimeType': 'application/json', 'temperature': 0.0,
                             'thinkingConfig': {'thinkingLevel': 'low'}, 'maxOutputTokens': 4096},
    }
    req = Request(url, data=json.dumps(body).encode('utf-8'), method='POST',
                  headers={'Content-Type': 'application/json', 'x-goog-api-key': key})
    try:
        with urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read(1_000_000).decode('utf-8'))
        parts = (payload.get('candidates') or [{}])[0].get('content', {}).get('parts', [])
        text = ''.join(p.get('text', '') for p in parts if not p.get('thought'))
        return str(_extract_json(text).get('english_text', '')).strip()
    except Exception:
        return ''


def _clean_trip(raw) -> dict:
    out = {'bus': '', 'time': '', 'from': '', 'to': ''}
    if isinstance(raw, dict):
        out['bus'] = re.sub(r'[^0-9A-Za-z -]', '', str(raw.get('bus_number', ''))).strip()[:12]
        out['time'] = str(raw.get('time', '')).strip()[:30]
        out['from'] = str(raw.get('from', '')).strip()[:60]
        out['to'] = str(raw.get('to', '')).strip()[:60]
    return out


def _clean_items(raw) -> list:
    out, seen = [], set()
    for it in (raw or []):
        if not isinstance(it, dict):
            continue
        name = re.sub(r'\s+', ' ', str(it.get('item', ''))).strip()[:60]
        color = re.sub(r'[^a-z ]', '', str(it.get('color', '')).lower()).strip()[:20]
        if not name:
            continue
        key = (name.lower(), color)
        if key in seen:
            continue
        seen.add(key)
        out.append({'item': name, 'color': color})
    return out[:40]


def transcribe(audio_path: Path, hint: str = '', model: str = '') -> dict:
    path = Path(audio_path)
    audio = path.read_bytes()
    if not audio:
        raise GeminiVoicemailError('Audio file is empty.')
    if len(audio) > MAX_AUDIO_BYTES:
        raise GeminiVoicemailError('Audio file is too large to send.')
    mime = MIME.get(path.suffix.lower(), 'audio/mp3')
    key = _read_key()
    used = (model or MODEL).strip()
    try:
        data = _call(key, used, audio, mime, hint)
    except GeminiVoicemailError as ex:
        if 'HTTP 404' not in str(ex) and 'HTTP 503' not in str(ex):
            raise
        if model:
            raise
        used = FALLBACK_MODEL
        data = _call(key, used, audio, mime, hint)
    original = str(data.get('original_text', '')).strip()
    if _looping(original):
        # One more try on the same audio; the prompt already forbids repeating.
        try:
            data = _call(key, used, audio, mime, hint + ' (The last attempt repeated itself. Write each word only once.)')
            original = str(data.get('original_text', '')).strip()
        except GeminiVoicemailError:
            pass
    unclear = [str(x) for x in (data.get('unclear') or []) if str(x).strip()][:20]
    if _looping(original):
        original = '[Recording unclear - the speech could not be read reliably. Please listen to the original audio.]'
        english = original
        unclear.append('whole message')
        items = []
    else:
        items = _clean_items(data.get('items'))
        english = str(data.get('english_text', '')).strip()
        if original and not english:
            english = _translate_text(key, used, original)
        if original and not english:
            english = '[English translation unavailable - please check the original]'
    return {
        'language': str(data.get('language', 'other')).strip().lower(),
        'original_text': original,
        'english_text': english,
        'callback_number': re.sub(r'\D', '', str(data.get('callback_number', ''))),
        'unclear': unclear,
        'items': items,
        'trip': _clean_trip(data.get('trip')),
    }


def transcribe_with_gemini(audio_path: Path, hint: str = '') -> tuple[str, str, str]:
    """Same return shape the listener already uses: (display text, raw text, language)."""
    global LAST_RESULT
    LAST_RESULT = {}
    r = transcribe(audio_path, hint)
    LAST_RESULT = r
    original, english = r['original_text'], r['english_text']
    lang = r['language']
    if lang == 'en' or not original or original == english:
        text = english or original
        return text, original or text, 'en' if lang in ('en', '') else lang
    shown_lang = 'yi' if lang in ('yi', 'mixed') else lang
    return f'{original}\n\nEnglish: {english}', original, shown_lang
