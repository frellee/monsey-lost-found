import email
import html
import json
import imaplib
import logging
import os
import re
import sqlite3
import tempfile
import time
from datetime import datetime
from email.header import decode_header, make_header
from pathlib import Path

from dotenv import load_dotenv
# GEMINI-VOICEMAIL-V1: the heavy local Whisper model now loads only if it is really needed.
from intermedia_dualtext import extract_intermedia_transcript

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
GMAIL = os.getenv('GMAIL_ADDRESS', '').strip()
PASSWORD = os.getenv('GMAIL_APP_PASSWORD', '').replace(' ', '').strip()
SENDER = os.getenv('VOICEMAIL_SENDER', 'voicemail@intermedia.com').strip()
POLL = max(30, int(os.getenv('POLL_SECONDS', '60')))
MODEL_SIZE = os.getenv('WHISPER_MODEL', 'small')
ENGINE = os.getenv('VOICEMAIL_ENGINE', 'gemini').strip().lower()  # 'gemini' or 'whisper'
GEMINI_MAX_TRIES = 3
FIRST_RUN_LIMIT = max(1, int(os.getenv('FIRST_RUN_LIMIT', '20')))
# Optional: set WHISPER_MODEL=medium in .env for more accurate but slower recognition.
VOCABULARY_HINT = ('Lost and found bus voicemail. AirPods, AirPod, earbuds, '
                   'Birkenstock, backpack, suitcase, Monsey, Boro Park, '
                   'Williamsburg, Flatbush, Lakewood, Monroe, Crown Heights.')


# MONSEY_HEIMISH_VOCABULARY_V1
# Extra context terms for local speech recognition; do not replace uncertain audio.
VOCABULARY_HINT += (' Talis, Tallis, Tallit, Tefillin, Tefilin, Tfillin, '
                    'Tefillin bag, Tallis bag, Siddur, Sefer, Seforim, Tehillim, '
                    'Chumash, Tzitzis, Yarmulke, Kippah, Shabbos, Shul, Mezuzah, '
                    'Monsey Trails, Boro Park, Williamsburg, Lakewood. ')

def clean_transcript(raw):
    """Fix only common, high-confidence item spellings; never guess names or numbers."""
    fixes = [
        (r'\bgear[ -]?pods\b', 'AirPods'),
        (r'\bair[ -]?pods\b', 'AirPods'),
        (r'\bair[ -]?pod\b', 'AirPod'),
        (r'\bear[ -]?pods\b', 'AirPods'),
    ]
    text = raw
    for pattern, replacement in fixes:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s', handlers=[logging.FileHandler(ROOT/'listener.log', encoding='utf-8'), logging.StreamHandler()])
log = logging.getLogger(__name__)

KEYWORDS = ('tefillin', 'tallis', 'iPad', 'AirPods', 'jewelry', 'טלית', 'תפילין', 'צירונגען', 'פארלוירן', 'פֿאַרלוירן', 'lost', 'missing', 'left', 'forgot', 'found', 'bag', 'wallet', 'phone', 'keys', 'purse', 'backpack', 'luggage', 'coat', 'jacket', 'glasses', 'umbrella', 'bus', 'פארלוירן', 'פארלויר', 'אבידה', 'אבד', 'שכחתי', 'נשכח')

_whisper_model = None


def get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        log.info('Loading local speech recognition model (%s). This is slow and heavy.', MODEL_SIZE)
        _whisper_model = WhisperModel(MODEL_SIZE, device='cpu', compute_type='int8')
    return _whisper_model


def transcribe_voicemail(path, key, failures):
    """Gemini first. If Gemini fails 3 times for one voicemail, use local Whisper as the backup.
    Returns (transcript, raw_transcript, language) or None to try again on the next check."""
    try:
        import gemini_voicemail
        gemini_voicemail.LAST_RESULT = {}
    except Exception:
        pass
    if ENGINE == 'gemini' and failures.get(key, 0) < GEMINI_MAX_TRIES:
        try:
            import gemini_voicemail
            return gemini_voicemail.transcribe_with_gemini(path, VOCABULARY_HINT)
        except Exception as exc:
            failures[key] = failures.get(key, 0) + 1
            log.warning('Gemini transcription failed (%s of %s): %s', failures[key], GEMINI_MAX_TRIES, exc)
            if failures[key] < GEMINI_MAX_TRIES:
                return None
            log.warning('Using the local backup recognizer for this voicemail.')
    from yiddish_bridge import transcribe_en_yiddish
    return transcribe_en_yiddish(path, get_whisper(), VOCABULARY_HINT)


def save_trip(filename, trip):
    """Keeps the bus / time / route the caller mentioned (if any)."""
    path = ROOT / 'voicemail_trips.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[filename] = trip
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, path)


def save_items(filename, items):
    """Keeps the item list Gemini heard, so the dashboards can show one box per voicemail."""
    path = ROOT / 'voicemail_items.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[filename] = items
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, path)


def decode(value):
    try:
        return str(make_header(decode_header(value or '')))
    except Exception:
        return str(value or '')

def notify(title, message):
    log.warning('%s: %s', title, message)
    try:
        from winotify import Notification
        Notification(app_id='Intermedia Lost & Found', title=title, msg=message[:230], duration='long').show()
    except Exception as exc:
        log.warning('Windows notification unavailable: %s', exc)

# INTERMEDIA-DUAL-SOURCE-V1
def write_readable_message(stamp, subject, filename, transcript, flagged, language, provider_text='', raw_transcript=''):
    from intermedia_dualtext import record_and_render
    return record_and_render(ROOT, stamp, subject, filename, transcript, flagged,
                             language, provider_text, raw_transcript)


def main():
    if not GMAIL or not PASSWORD:
        raise RuntimeError('First fill in GMAIL_ADDRESS and GMAIL_APP_PASSWORD in the .env file. Read START_HERE.txt.')
    log.info('Voicemail engine: %s', 'Gemini (paid Google API, local Whisper only as backup)' if ENGINE == 'gemini' else 'local Whisper')
    failures = {}
    db = sqlite3.connect(ROOT/'processed.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS done (uid TEXT, filename TEXT, PRIMARY KEY(uid,filename))')
    db.commit()
    log.info('Listening for voicemail. Keep this window open. Ctrl+C stops the listener.')
    while True:
        mail = None
        try:
            mail = imaplib.IMAP4_SSL('imap.gmail.com', 993)
            mail.login(GMAIL, PASSWORD)
            status, _ = mail.select('INBOX', readonly=True)
            if status != 'OK':
                raise RuntimeError('Could not open Gmail inbox.')
            status, result = mail.uid('search', None, 'FROM', f'"{SENDER}"')
            if status != 'OK':
                raise RuntimeError('Gmail search failed.')
            uids = result[0].split()
            # Only a bounded recent window; stored IDs prevent duplicates across restarts.
            for uid in uids[-FIRST_RUN_LIMIT:]:
                status, data = mail.uid('fetch', uid, '(BODY.PEEK[])')
                if status != 'OK':
                    continue
                raw = next((part[1] for part in data if isinstance(part, tuple) and isinstance(part[1], bytes)), None)
                if not raw:
                    continue
                msg = email.message_from_bytes(raw)
                subject = decode(msg.get('Subject', ''))
                intermedia_text = extract_intermedia_transcript(msg)
                for part in msg.walk():
                    name = decode(part.get_filename())
                    mime = part.get_content_type().lower()
                    if not name or not (name.lower().endswith(('.mp3', '.wav', '.m4a', '.ogg', '.webm')) or mime.startswith('audio/')):
                        continue
                    uid_text = uid.decode('ascii')
                    if db.execute('SELECT 1 FROM done WHERE uid=? AND filename=?', (uid_text, name)).fetchone():
                        continue
                    audio = part.get_payload(decode=True)
                    if not audio:
                        continue
                    suffix = Path(name).suffix.lower() or '.mp3'
                    log.info('Transcribing voicemail: %s', name)
                    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                        tmp.write(audio)
                        path = Path(tmp.name)
                    try:
                        result = transcribe_voicemail(path, uid_text + '|' + name, failures)
                    finally:
                        path.unlink(missing_ok=True)
                    if result is None:
                        continue
                    transcript, raw_transcript, detected_language = result
                    try:
                        import gemini_voicemail
                        heard = (gemini_voicemail.LAST_RESULT or {}).get('items')
                        if heard is not None:
                            save_items(name, heard)
                        trip = (gemini_voicemail.LAST_RESULT or {}).get('trip')
                        if trip and any(trip.values()):
                            save_trip(name, trip)
                    except Exception as exc:
                        log.warning('Could not save the item list: %s', exc)
                    if not transcript:
                        transcript = '[No intelligible speech detected]'
                    flagged = any(k in transcript.lower() for k in KEYWORDS)
                    stamp = datetime.now().astimezone().strftime('%Y-%m-%d %I:%M %p')
                    write_readable_message(stamp, subject, name, transcript, flagged, detected_language,
                                           provider_text=intermedia_text, raw_transcript=raw_transcript)
                    record = f'\n--- {stamp} ---\nSubject: {subject}\nAttachment: {name}\nLikely lost and found: {flagged}\nTranscript: {transcript}\n'
                    with (ROOT/'transcripts.txt').open('a', encoding='utf-8') as f:
                        f.write(record)
                    if raw_transcript != transcript:
                        with (ROOT/'transcripts_original.txt').open('a', encoding='utf-8') as f:
                            f.write(f'\n--- {stamp} ---\nOriginal: {raw_transcript}\nCorrected: {transcript}\n')
                    db.execute('INSERT OR IGNORE INTO done(uid,filename) VALUES (?,?)', (uid_text, name))
                    db.commit()
                    if flagged:
                        notify('Possible lost & found voicemail', transcript)
                    else:
                        log.info('Transcribed (no lost-item alert): %s', transcript[:160])
            mail.logout()
            mail = None
        except KeyboardInterrupt:
            log.info('Stopped.')
            return
        except Exception as exc:
            log.exception('Connection or transcription problem: %s', exc)
        finally:
            if mail is not None:
                try:
                    mail.logout()
                except Exception:
                    pass
        time.sleep(POLL)


# MONSEY-COLORED-ITEMS-V1
_original_voicemail_writer_for_item_sheet = write_readable_message
def write_readable_message(*args, **kwargs):
    result = _original_voicemail_writer_for_item_sheet(*args, **kwargs)
    try:
        from lost_found_sheet import rebuild
        rebuild(ROOT)
    except Exception as _sheet_exception:
        log.warning('Lost & Found item sheet could not refresh: %s', _sheet_exception)
    return result

if __name__ == '__main__':
    main()
