"""Monsey Trails Gemini photo identification (V14 - allow the first Gemini request time to finish).

Only the found-item photograph and a generic item-identification prompt are sent
via HTTPS to Google's Gemini API. The existing key is loaded from the separate
local gemini_api_key.txt file or MONSEY_GEMINI_API_KEY environment variable.
No Gmail, owner names, phone numbers, record IDs, saved paths or voicemail data
are sent. This module does not change the user's saved records or pictures.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
MODEL = 'gemini-3.8-flash'
FALLBACK_MODEL = 'gemini-3.5-flash'
URL = 'https://generativelanguage.googleapis.com/v1beta/models/' + MODEL + ':generateContent'
KEY_FILE = ROOT / 'gemini_api_key.txt'
LOG_FILE = ROOT / 'gemini_connection.log'
# High-resolution photo analysis and medium reasoning can require more than 75 seconds.
# A short client timeout was making a valid first request look like a network failure.
REQUEST_TIMEOUT_SECONDS = 180

PROMPT = """You are identifying ONE found property item from a photograph for a bus lost-and-found office.
Look at the OBJECT, not the table, packaging reflections or background. Describe visible evidence only.
Recognize any type of personal property, including everyday consumer products and Jewish religious items.
Examples of useful distinctions: a closed flip phone is still a phone; a remote control is not a phone;
Apple AirPods charging case versus generic earbud case; siddur (Jewish prayer book) when visible Hebrew
prayer-book markings justify it, versus a generic book; tefillin and tefillin bag versus an ordinary box;
tallis/tallit versus scarf; wallet versus card holder. Do not force any example label.
Use the most specific supported item name, not a generic 'toy', 'thing' or 'object'.
If identifying the specific brand/model is not supported by visible markings, don't guess it.
Do NOT guess unreadable writing, unseen sides, owner, value, or whether the item was lost.
Determine the COLOR OF THE OBJECT itself, not its surroundings or light reflection.
Distinguish black, white, gray, silver, red, maroon, navy, blue, teal, turquoise,
green, olive, yellow, orange, pink, purple, brown, tan, cream, gold, etc.
If genuinely multicolored, use 'multicolor' and name the visible predominant colors.
Return ONLY a JSON object of this form with no code fences:
{
  "item": "2 to 5 word precise English item name, e.g. Flip phone",
  "color": "one simple lowercase color name or multicolor or unknown",
  "color_hex": "#RRGGBB representing the actual OBJECT color for coloring the ITEM WORD; blank if unknown",
  "color_palette_hex": ["#RRGGBB", "#RRGGBB"],
  "description": "one short factual sentence describing the visible item",
  "certainty": "high or medium or low"
}
Choose high when clearly identifiable, medium when likely identifiable but details are uncertain,
and low only if the broad object type truly cannot be identified. Do not lower certainty merely
because the exact brand or model is unknown. Never invent details.
On this FIRST response, examine the entire image carefully before naming the object. Compare
shape, material, visible markings, and functional details; distinguish lookalike objects.
Return the best supported specific item CATEGORY even if its manufacturer cannot be determined.
For a recognizably ordinary item such as a siddur, umbrella, glove, wallet, phone, bag, keys,
or eyeglasses, give a direct category answer without requiring a second look. Do not guess
an item that the image does not support. Do all this analysis within one response."""

SECOND_LOOK = """Review this SAME photograph carefully for accurate lost-property classification.
The first pass was uncertain. Examine its overall shape, edges, buttons, texture, writing,
religious markings, and other visible distinguishing features. Identify the most specific
SUPPORTED object type. Do not guess brands, hidden features, or unreadable text.
Use the same JSON fields requested above. Choose low certainty if the object remains ambiguous."""


class GeminiError(RuntimeError):
    pass


def _log(message: str) -> None:
    """Record connection status, not image contents, prompts, keys, or responses."""
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {message}"
    print(line, flush=True)
    try:
        with LOG_FILE.open('a', encoding='utf-8') as f:
            f.write(line + '\n')
    except OSError:
        pass  # The app still works when the folder is read-only.


def _read_key() -> str:
    key = os.environ.get('MONSEY_GEMINI_API_KEY', '').strip()
    if not key and KEY_FILE.is_file():
        contents = KEY_FILE.read_text(encoding='utf-8-sig').strip()
        key = contents.splitlines()[0].strip() if contents else ''
    if not key or key.lower().startswith(('paste ', 'put ', 'your ', '#')):
        raise GeminiError('Gemini key is not set. Run SETUP_GEMINI_KEY.bat in the Found Items folder, then retry.')
    if not re.fullmatch(r'[A-Za-z0-9._\-]{16,256}', key):
        raise GeminiError('The saved Gemini API key appears invalid. Check SETUP_GEMINI_KEY.bat.')
    return key


def _prepare_image(path: Path) -> str:
    # Keep the original image untouched and send only a resized copy.
    from PIL import Image, ImageOps
    with Image.open(path) as raw:
        if raw.width * raw.height > 48_000_000:
            raise GeminiError('Photo resolution is too large. Please choose a smaller image.')
        img = ImageOps.exif_transpose(raw).convert('RGB')
        img.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
        buff = io.BytesIO()
        img.save(buff, format='JPEG', quality=90, optimize=True)
        encoded = base64.b64encode(buff.getvalue()).decode('ascii')
        if len(encoded) > 14_000_000:
            raise GeminiError('Photo too large for online analysis.')
        return encoded


def _hex(value: object) -> str:
    s = str(value or '').strip()
    return s.upper() if re.fullmatch(r'#[0-9a-fA-F]{6}', s) else ''


def _parse_response(payload: dict, model: str = MODEL) -> dict:
    try:
        candidate = payload['candidates'][0]
        parts = candidate['content']['parts']
        raw_text = ''.join(str(p.get('text', '')) for p in parts
                           if isinstance(p, dict) and not p.get('thought'))
        raw_text = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw_text.strip(), flags=re.I).strip()
        obj = json.loads(raw_text)
    except (IndexError, KeyError, TypeError, ValueError) as ex:
        reason = ''
        try:
            reason = payload.get('candidates', [{}])[0].get('finishReason', '')
        except (TypeError, IndexError, AttributeError):
            pass
        if reason == 'MAX_TOKENS':
            raise GeminiError('Gemini ran out of response tokens before finishing the item description.') from ex
        if reason in ('SAFETY', 'PROHIBITED_CONTENT', 'BLOCKLIST'):
            raise GeminiError('Gemini could not analyze this photo because its response was blocked.') from ex
        raise GeminiError('Google did not return a readable item description.') from ex
    if not isinstance(obj, dict):
        raise GeminiError('Gemini returned an unexpected item description.')
    name = str(obj.get('item') or '').strip()[:80]
    if len(name) < 2:
        raise GeminiError('AI could not identify the item from this photograph.')
    color = str(obj.get('color') or '').strip().lower()[:24]
    if color in ('unknown', 'none', 'not sure', 'unclear'):
        color = ''
    color_hex = _hex(obj.get('color_hex'))
    palette = []
    if color == 'multicolor':
        raw_palette = obj.get('color_palette_hex')
        if isinstance(raw_palette, list):
            palette = [_hex(c) for c in raw_palette if isinstance(c, str)][:5]
            palette = [c for c in palette if c]
        if len(palette) < 2:
            color, palette = '', []
    certainty = str(obj.get('certainty') or '').strip().lower()
    if certainty not in ('high', 'medium', 'low'):
        certainty = 'low'
    description = str(obj.get('description') or '').strip()[:260]
    return {
        'item': name, 'color': color, 'color_hex': color_hex,
        'color_palette_hex': palette, 'caption': description,
        'recognition_state': 'review' if certainty == 'low' else 'suggested',
        'recognition_note': ('Google Gemini photo suggestion (' + certainty + ' confidence). ' +
                             description + ' Confirm before returning property.')[:400],
        'ai_provider': 'Gemini', 'ai_model': model,
    }


def _call_model(key: str, encoded: str, prompt: str, model: str) -> dict:
    url = 'https://generativelanguage.googleapis.com/v1beta/models/' + model + ':generateContent'
    body = {
        'contents': [{'role': 'user', 'parts': [
            {'text': prompt},
            {'inline_data': {'mime_type': 'image/jpeg', 'data': encoded}},
        ]}],
        'generationConfig': {
            'responseMimeType': 'application/json', 'temperature': 0.1,
            'thinkingConfig': {'thinkingLevel': 'medium'},
            'mediaResolution': 'MEDIA_RESOLUTION_HIGH',
            # Medium reasoning improves first-pass care; reserve room for internal thinking and the JSON answer.
            'maxOutputTokens': 8192,
        },
    }
    req = Request(url, data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                  headers={'Content-Type': 'application/json', 'x-goog-api-key': key},
                  method='POST')
    # Repeat only transient failures, never a bad key or exceeded daily quota.
    waits = (3, 8)
    for attempt in range(len(waits) + 1):
        started = time.monotonic()
        try:
            with urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                raw = response.read(300_000)
            payload = json.loads(raw.decode('utf-8'))
            result = _parse_response(payload, model)
            if attempt:
                _log(f'Gemini {model}: request succeeded on attempt {attempt + 1}.')
            return result
        except HTTPError as ex:
            code = ex.code
            _log(f'Gemini {model}: HTTP {code} on attempt {attempt + 1}.')
            if code in (500, 502, 503, 504) and attempt < len(waits):
                time.sleep(waits[attempt])
                continue
            if code == 404:
                raise GeminiError(f'Gemini model {model} returned HTTP 404 (not found or not available to this key).') from None
            if code in (400, 401, 403):
                raise GeminiError(f'Gemini HTTP {code}: invalid request, API key, or permission; check Google AI Studio.') from None
            if code == 429:
                raise GeminiError('Gemini HTTP 429: quota or rate limit reached. Check AI Studio usage limits.') from None
            raise GeminiError(f'Gemini model {model} returned HTTP {code}.') from None
        except (URLError, TimeoutError, OSError) as exc:
            # A local timeout can happen even while Google's server is still working.
            # Log the cause without logging API keys, private paths or photo contents.
            reason = exc.reason if isinstance(exc, URLError) else exc
            elapsed = round(time.monotonic() - started)
            is_timeout = isinstance(reason, TimeoutError) or 'timed out' in str(reason).lower()
            cause = (f'timed out after {elapsed}s' if is_timeout
                     else f'connection error ({type(reason).__name__}) after {elapsed}s')
            _log(f'Gemini {model}: {cause} on attempt {attempt + 1}.')
            if attempt < len(waits):
                time.sleep(waits[attempt])
                continue
            raise GeminiError(
                f'Could not complete Gemini model {model} request ({cause}). '
                'The photo remains saved; please check your internet connection or Gemini availability.'
            ) from None
        except (ValueError, UnicodeError):
            _log(f'Gemini {model}: unreadable server response.')
            raise GeminiError('Gemini returned an unreadable response.') from None
    raise GeminiError(f'Gemini model {model} did not respond.')


def _identify(key: str, encoded: str, prompt: str) -> dict:
    try:
        return _call_model(key, encoded, prompt, MODEL)
    except GeminiError as exc:
        message = str(exc)
        # Try a verified image-capable fallback on unavailable model / server.
        # Do not switch models to evade rate limits or API key problems.
        if not any(term in message for term in ('HTTP 404', 'HTTP 500', 'HTTP 502', 'HTTP 503',
                                                'HTTP 504', 'Could not reach Gemini model')):
            raise
        _log(f'Falling back from {MODEL} to {FALLBACK_MODEL}.')
        try:
            return _call_model(key, encoded, prompt, FALLBACK_MODEL)
        except GeminiError as fallback_exc:
            raise GeminiError(f'Primary Gemini model failed ({message}); fallback failed ({fallback_exc}).') from None


def analyze(path: Path) -> dict:
    key = _read_key()
    encoded = _prepare_image(Path(path))
    result = _identify(key, encoded, PROMPT)
    _log('Gemini: initial photo result completed (' + result['recognition_state'] + ').')
    if result['recognition_state'] == 'review':
        # Accuracy first: only uncertain images get one automatic second look.
        # This completes within the SAME upload; users need not press Recheck.
        _log('Gemini: first result uncertain; automatically checking same photo once more.')
        try:
            second = _identify(key, encoded, PROMPT + '\n\n' + SECOND_LOOK)
            if second['recognition_state'] == 'suggested':
                _log('Gemini: second look identified the object more confidently.')
                return second
            _log('Gemini: object still uncertain after second look; manual review needed.')
        except GeminiError as exc:
            _log('Gemini: second look unavailable; keeping initial low-confidence result.')
        result['recognition_note'] = (
            result['recognition_note'] + ' The automatic second check could not confirm the item.'
        )[:400]
    return result
