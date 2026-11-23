"""Intermedia voicemail dual-source dashboard (local, no external service).

Keeps both independently produced transcripts. Does NOT claim differences have been
resolved by another ASR model; uncertain phone numbers/names require audio review.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from email.message import Message
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import re


class _HTMLToText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
    def handle_data(self, data):
        self.parts.append(data)
    def handle_starttag(self, tag, attrs):
        if tag.lower() in ('br', 'p', 'div', 'li', 'tr'):
            self.parts.append('\n')
    def get_text(self):
        return ''.join(self.parts)


def extract_intermedia_transcript(message: Message) -> str:
    """Read only provider's transcript from text/plain, or HTML fallback.

    Ignore the email template and legal footer; do not confuse those with speech.
    """
    parts = []
    for part in message.walk():
        if part.is_multipart():
            continue
        if part.get_content_disposition() == 'attachment' or part.get_filename():
            continue
        kind = part.get_content_type().lower()
        if kind not in ('text/plain', 'text/html'):
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                text = str(part.get_payload() or '')
            else:
                text = payload.decode(part.get_content_charset() or 'utf-8', errors='replace')
            if kind == 'text/html':
                parser = _HTMLToText()
                parser.feed(text)
                text = parser.get_text()
            parts.append((kind != 'text/plain', text))
        except Exception:
            continue
    for _, text in sorted(parts, key=lambda it: it[0]):
        match = re.search(
            r'(?ims)^\s*Voicemail\s+transcript\s*:\s*(.*?)\s*'
            r'(?=^\s*(?:The attached voicemail message|To delete the message|'
            r'Make calls via|Thank you for being|Copyright|\*Note\s*:)|\Z)',
            text,
        )
        if match:
            result = re.sub(r'\s+', ' ', match.group(1)).strip()
            if result:
                return result[:12000]
    return ''


def _phones(text: str) -> list[str]:
    # Accept only exactly 10-digit US phone numbers, optionally starting with 1.
    # Do not guess digits from partially recognized numbers.
    candidates = re.findall(r'(?<!\d)(?:\+?1[ .()-]*)?(?:\(?\d{3}\)?[ .()-]*)\d{3}[ .()-]*\d{4}(?!\d)', text)
    result = []
    for candidate in candidates:
        digits = re.sub(r'\D', '', candidate)
        if len(digits) == 11 and digits.startswith('1'):
            digits = digits[1:]
        if len(digits) != 10:
            continue
        formatted = f'{digits[:3]}-{digits[3:6]}-{digits[6:]}'
        if formatted not in result:
            result.append(formatted)
    return result


def _caller_id(subject: str) -> str:
    match = re.search(r'\(\+?1?\s*([0-9]{10})\)', subject)
    if not match:
        return ''
    digits = match.group(1)
    return f'{digits[:3]}-{digits[3:6]}-{digits[6:]}'


def _agreement(a: str, b: str) -> float | None:
    if not a or not b:
        return None
    normal = lambda s: re.findall(r"[a-z0-9']+", s.casefold())
    x, y = normal(a), normal(b)
    if not x or not y:
        return None
    return round(SequenceMatcher(None, x, y, autojunk=False).ratio(), 2)


def _review(audio_text: str, provider: str, lang: str, subject: str):
    aphones = _phones(audio_text)
    pphones = _phones(provider)
    caller_id = _caller_id(subject)
    if aphones:
        phone, label = aphones[-1], 'Phone heard by local recognizer — verify against audio'
    elif pphones and lang == 'en':
        phone, label = pphones[-1], 'Phone found in Intermedia transcript — verify against audio'
    elif caller_id:
        phone, label = caller_id, 'Caller ID only — may not be callback number'
    else:
        phone, label = 'Not reliably recognized', 'Check the original audio'
    notes = []
    score = _agreement(audio_text, provider) if lang == 'en' else None
    if lang == 'en' and provider:
        if score is not None and score < .65:
            notes.append('The two English transcripts differ substantially. Verify important words from the recording.')
        if aphones and pphones and not (set(aphones) & set(pphones)):
            notes.append('The two transcripts disagree on the phone number. Do not call an unverified number.')
    elif lang == 'yi' and provider:
        notes.append('Intermedia may mishear Yiddish as English. Do not automatically replace Yiddish with its transcript.')
    if not provider:
        notes.append('No Intermedia transcript found in the email for this recording.')
    return phone, label, caller_id, score, notes


def record_and_render(root: Path, stamp: str, subject: str, filename: str,
                      transcript: str, flagged: bool, language: str,
                      provider_text: str = '', raw_transcript: str = '') -> None:
    root = Path(root)
    data_path = root / 'voicemail_messages.json'
    page_path = root / 'voicemail_messages.html'
    try:
        records = json.loads(data_path.read_text(encoding='utf-8')) if data_path.exists() else []
        if not isinstance(records, list):
            records = []
    except (OSError, ValueError):
        records = []
    phone, phone_label, caller_id, score, notes = _review(transcript, provider_text, language, subject)
    entry = dict(date=stamp, subject=subject, filename=filename, transcript=transcript,
                 flagged=flagged, language=language, phone=phone, phone_label=phone_label,
                 caller_id=caller_id, provider_transcript=provider_text, original_audio_transcript=raw_transcript,
                 agreement=score, review_notes=notes)
    # Keep existing records unmodified; do not overwrite any previous voicemail.
    records.insert(0, entry)
    records = records[:500]
    new_json = json.dumps(records, ensure_ascii=False, indent=2)
    _atomic_write(data_path, new_json)
    _render(page_path, records)


def _atomic_write(target: Path, value: str):
    import os
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n', dir=str(target.parent),
                                     prefix='.voicemail_', suffix='.tmp', delete=False) as f:
        path = Path(f.name)
        f.write(value)
    try:
        os.replace(path, target)
    finally:
        path.unlink(missing_ok=True)


def _render(path: Path, records: list[dict]):
    esc = lambda value: escape(str(value or ''), quote=True)
    cards = []
    for e in records:
        lang = e.get('language', 'unknown')
        direction = 'rtl' if lang in ('yi', 'he') else 'ltr'
        status = 'Possible lost & found' if e.get('flagged') else 'Other voicemail'
        mark = 'urgent' if e.get('flagged') else 'ordinary'
        provider = e.get('provider_transcript', '')
        raw = e.get('original_audio_transcript', '')
        existing_notes = e.get('review_notes', [])
        if not isinstance(existing_notes, list):
            existing_notes = []
        notes = ''.join('<li>' + esc(note) + '</li>' for note in existing_notes)
        notes_html = '<ul class="review">' + notes + '</ul>' if notes else ''
        provider_html = ('<p dir="ltr" class="source">' + esc(provider) + '</p>' if provider
                         else '<p class="muted">Not found in this email, or this is an earlier saved message.</p>')
        raw_html = ('<p dir="' + direction + '" class="source">' + esc(raw) + '</p>' if raw else '')
        phone = e.get('phone', 'Not reliably recognized')
        phone_label = e.get('phone_label', 'Phone recognized by software — verify against recording')
        cid = e.get('caller_id', '')
        caller_html = '<p class="muted">Caller ID: ' + esc(cid) + ' (not verified callback number)</p>' if cid else ''
        cards.append(f'''<article class="card">
<header><span class="status {mark}">{esc(status)}</span><time>{esc(e.get('date'))}</time></header>
<p class="tiny">{esc(phone_label)}</p><p class="phone" dir="ltr">{esc(phone)}</p>{caller_html}
<div class="heading">Audio-based transcript</div>
<div class="transcript" dir="{direction}" lang="{esc(lang)}">{esc(e.get('transcript'))}</div>
{notes_html}
<details><summary>Compare with Intermedia's email transcript</summary>
<div class="heading">Intermedia email transcript (second opinion, may be wrong)</div>
{provider_html}
{('<div class="heading">Original local transcript</div>' + raw_html) if raw and raw != e.get('transcript') else ''}
<p class="tiny">Original email subject: {esc(e.get('subject'))}</p>
<p class="tiny">Audio attachment: {esc(e.get('filename'))}</p>
<p class="tiny">Language detected: {esc(lang)}</p>
</details></article>''')
    html_page = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Intermedia Voicemail Messages</title><style>
*{box-sizing:border-box}body{margin:0;background:#f3f5f9;color:#14243b;font-family:Segoe UI,Arial,sans-serif}
main{max-width:940px;margin:30px auto;padding:0 16px}h1{margin:0;font-size:29px}.lead{color:#64748b;margin:9px 0 25px}
.card{background:#fff;border:1px solid #dce4ef;border-radius:15px;padding:24px;margin:15px 0;box-shadow:0 2px 8px #14243b0d}
header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px}
.status{font-size:12px;font-weight:700;padding:7px 11px;border-radius:20px}.urgent{color:#923f21;background:#fff0e6}.ordinary{color:#334155;background:#eef2f7}
time,.tiny{font-size:12px;color:#64748b}.tiny{margin-bottom:4px}.phone{font-size:23px;font-weight:700;margin:4px 0;overflow-wrap:anywhere}
.muted{color:#6b7280;font-size:13px}.heading{font-weight:700;font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:.06em;margin:24px 0 8px}
.transcript{font-size:21px;line-height:1.8;white-space:pre-wrap;unicode-bidi:plaintext;overflow-wrap:anywhere}
.source{font-size:16px;line-height:1.65;white-space:pre-wrap;overflow-wrap:anywhere}
details{margin-top:18px;border-top:1px solid #e2e8f0;padding-top:16px}summary{cursor:pointer;font-weight:600;color:#23456d}
.review{background:#fff8ed;border-radius:7px;margin-top:14px;padding:10px 14px 10px 32px;color:#75481c;font-size:14px}
</style></head><body><main><h1>Intermedia Voicemail Messages</h1>
<p class="lead">Audio recognition and Intermedia's email transcript are shown separately. Names, numbers and lost items can be misheard. Confirm important details against the recording.</p>'''
    _atomic_write(path, html_page + '\n'.join(cards) + '</main></body></html>')


def refresh_existing_dashboard(root: Path) -> None:
    """Reformat existing entries without altering their stored text."""
    root = Path(root)
    data_path = root / 'voicemail_messages.json'
    if data_path.is_file():
        records = json.loads(data_path.read_text(encoding='utf-8'))
        if not isinstance(records, list):
            raise ValueError('Unexpected voicemail_messages.json format')
        _render(root / 'voicemail_messages.html', records[:500])
