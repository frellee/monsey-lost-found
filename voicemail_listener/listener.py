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
from datetime import date, datetime, timedelta
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
LOOKBACK_DAYS = max(0, int(os.getenv('LOOKBACK_DAYS', '14')))
IMAP_MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
               'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
# Attachment filenames are always nonempty. Legacy rows still identify individual files.
UID_COMPLETE = ''
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


def initialize_processing_db(db):
    with db:
        db.execute('''
            CREATE TABLE IF NOT EXISTS done (
                uid TEXT, filename TEXT, PRIMARY KEY (uid, filename)
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS failures (
                uid TEXT, filename TEXT, count INTEGER, last_error TEXT,
                updated_at TEXT, PRIMARY KEY (uid, filename)
            )
        ''')


def select_uids_to_process(all_uids, processed_uids, is_first_run, first_run_limit):
    """Select unfinished IMAP UIDs, ordered by arrival (ascending numeric UID)."""
    pending = sorted(set(all_uids).difference(processed_uids), key=int)
    return pending[-first_run_limit:] if is_first_run else pending


def imap_since(today, lookback_days):
    cutoff = today - timedelta(days=lookback_days)
    month = IMAP_MONTHS[cutoff.month - 1]
    return f'{cutoff.day:02d}-{month}-{cutoff.year:04d}'


def record_gemini_failure(db, uid, filename, error_type):
    with db:
        db.execute('''
            INSERT INTO failures (uid, filename, count, last_error, updated_at)
            VALUES (?, ?, 1, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (uid, filename) DO UPDATE SET
                count = failures.count + 1,
                last_error = excluded.last_error,
                updated_at = excluded.updated_at
        ''', (uid, filename, error_type))
        return db.execute(
            'SELECT count FROM failures WHERE uid=? AND filename=?', (uid, filename)
        ).fetchone()[0]


def transcribe_voicemail(path, uid, filename, db):
    """Try Gemini up to the saved retry limit, then fall back to local Whisper."""
    try:
        import gemini_voicemail
        gemini_voicemail.LAST_RESULT = {}
    except Exception:
        pass

    if ENGINE == 'gemini':
        failure = db.execute(
            'SELECT count FROM failures WHERE uid=? AND filename=?', (uid, filename)
        ).fetchone()
        if failure is None or failure[0] < GEMINI_MAX_TRIES:
            try:
                import gemini_voicemail
                return gemini_voicemail.transcribe_with_gemini(path, VOCABULARY_HINT)
            except Exception as exc:
                # Provider error messages can contain secrets or private voicemail text.
                error_type = type(exc).__name__
                count = record_gemini_failure(db, uid, filename, error_type)
                log.warning('Gemini transcription failed (%s of %s): %s',
                            count, GEMINI_MAX_TRIES, error_type)
                if count < GEMINI_MAX_TRIES:
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
    log.warning('%s', title)
    try:
        from winotify import Notification
        Notification(app_id='Intermedia Lost & Found', title=title, msg=message[:230], duration='long').show()
    except Exception as exc:
        log.warning('Windows notification unavailable: %s', type(exc).__name__)

# INTERMEDIA-DUAL-SOURCE-V1
def write_readable_message(stamp, subject, filename, transcript, flagged, language,
                           provider_text='', raw_transcript='', uid=''):
    from intermedia_dualtext import record_and_render
    return record_and_render(ROOT, stamp, subject, filename, transcript, flagged,
                             language, provider_text, raw_transcript, uid=uid)


def process_attachment(db, uid, name, part, subject, intermedia_text):
    audio = part.get_payload(decode=True)
    if not audio:
        return False

    suffix = Path(name).suffix.lower() or '.mp3'
    log.info('Transcribing voicemail attachment.')
    path = None
    try:
        # Close the file before a recognizer opens it on Windows.
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            path = Path(tmp.name)
            tmp.write(audio)
        result = transcribe_voicemail(path, uid, name, db)
    finally:
        if path is not None:
            path.unlink(missing_ok=True)
    if result is None:
        return False

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
        log.warning('Could not save voicemail item or trip details: %s', type(exc).__name__)
    if not transcript:
        transcript = '[No intelligible speech detected]'

    flagged = any(keyword in transcript.lower() for keyword in KEYWORDS)
    stamp = datetime.now().astimezone().strftime('%Y-%m-%d %I:%M %p')
    write_readable_message(stamp, subject, name, transcript, flagged, detected_language,
                           provider_text=intermedia_text, raw_transcript=raw_transcript,
                           uid=uid)
    record = (f'\n--- {stamp} ---\nSubject: {subject}\nAttachment: {name}\n'
              f'Likely lost and found: {flagged}\nTranscript: {transcript}\n')
    with (ROOT / 'transcripts.txt').open('a', encoding='utf-8') as history:
        history.write(record)
    if raw_transcript != transcript:
        with (ROOT / 'transcripts_original.txt').open('a', encoding='utf-8') as history:
            history.write(f'\n--- {stamp} ---\nOriginal: {raw_transcript}\nCorrected: {transcript}\n')

    with db:
        db.execute('INSERT OR IGNORE INTO done (uid, filename) VALUES (?, ?)', (uid, name))
        db.execute('DELETE FROM failures WHERE uid=? AND filename=?', (uid, name))
    if flagged:
        notify('Possible lost & found voicemail', transcript)
    else:
        log.info('Transcribed voicemail (no lost-item alert).')
    return True


def process_uid(mail, db, uid):
    status, data = mail.uid('fetch', uid, '(BODY.PEEK[])')
    if status != 'OK':
        return
    raw = next((part[1] for part in data
                if isinstance(part, tuple) and isinstance(part[1], bytes)), None)
    if not raw:
        return

    message = email.message_from_bytes(raw)
    subject = decode(message.get('Subject', ''))
    intermedia_text = extract_intermedia_transcript(message)
    completed_files = {row[0] for row in db.execute('SELECT filename FROM done WHERE uid=?', (uid,))}
    has_pending_audio = False
    for part in message.walk():
        name = decode(part.get_filename())
        is_audio = (name.lower().endswith(('.mp3', '.wav', '.m4a', '.ogg', '.webm'))
                    or part.get_content_type().startswith('audio/'))
        if not name or not is_audio or name in completed_files:
            continue
        try:
            if process_attachment(db, uid, name, part, subject, intermedia_text):
                completed_files.add(name)
            else:
                has_pending_audio = True
        except Exception as exc:
            log.warning('Could not process voicemail attachment: %s', type(exc).__name__)
            has_pending_audio = True

    # A saved attachment alone does not prove its siblings were saved successfully.
    if completed_files and not has_pending_audio:
        with db:
            db.execute('INSERT OR IGNORE INTO done (uid, filename) VALUES (?, ?)',
                       (uid, UID_COMPLETE))


def poll_voicemails(mail, db):
    since = imap_since(date.today(), LOOKBACK_DAYS)
    status, result = mail.uid('search', None, 'FROM', f'"{SENDER}"', 'SINCE', since)
    if status != 'OK':
        raise RuntimeError('Gmail search failed.')

    all_uids = result[0].decode('ascii').split() if result and result[0] else []
    is_first_run = db.execute('SELECT 1 FROM done LIMIT 1').fetchone() is None
    processed_uids = {row[0] for row in db.execute(
        'SELECT uid FROM done WHERE filename=?', (UID_COMPLETE,)
    )}
    for uid in select_uids_to_process(all_uids, processed_uids, is_first_run, FIRST_RUN_LIMIT):
        process_uid(mail, db, uid)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(message)s',
        handlers=[logging.FileHandler(ROOT / 'listener.log', encoding='utf-8'),
                  logging.StreamHandler()],
    )
    if not GMAIL or not PASSWORD:
        raise RuntimeError('First fill in GMAIL_ADDRESS and GMAIL_APP_PASSWORD in the .env file. Read START_HERE.txt.')
    engine_label = ('Gemini (paid Google API, local Whisper only as backup)'
                    if ENGINE == 'gemini' else 'local Whisper')
    log.info('Voicemail engine: %s', engine_label)
    db = sqlite3.connect(ROOT / 'processed.sqlite3')
    try:
        initialize_processing_db(db)
        log.info('Listening for voicemail. Keep this window open. Ctrl+C stops the listener.')
        while True:
            mail = None
            try:
                mail = imaplib.IMAP4_SSL('imap.gmail.com', 993)
                mail.login(GMAIL, PASSWORD)
                status, _ = mail.select('INBOX', readonly=True)
                if status != 'OK':
                    raise RuntimeError('Could not open Gmail inbox.')
                poll_voicemails(mail, db)
            except Exception as exc:
                log.warning('Voicemail poll failed: %s', type(exc).__name__)
            finally:
                if mail is not None:
                    try:
                        mail.logout()
                    except Exception:
                        pass
            time.sleep(POLL)
    except KeyboardInterrupt:
        log.info('Stopped.')
    finally:
        db.close()


# MONSEY-COLORED-ITEMS-V1
_original_voicemail_writer_for_item_sheet = write_readable_message
def write_readable_message(*args, **kwargs):
    result = _original_voicemail_writer_for_item_sheet(*args, **kwargs)
    try:
        from lost_found_sheet import rebuild
        rebuild(ROOT)
    except Exception as _sheet_exception:
        log.warning('Lost & Found item sheet could not refresh: %s', type(_sheet_exception).__name__)
    return result

if __name__ == '__main__':
    main()
