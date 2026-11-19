"""START FRESH: clear the old voicemails from the dashboard, then only NEW voicemails arrive.

Nothing is deleted. The old voicemail data is MOVED into  Old_Backups\\fresh_<date>  inside this folder.
Your Gmail is not changed (the program only reads it). Found items, photos and customer notes are not touched.
"""
import email
import imaplib
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from email.header import decode_header, make_header
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Files the voicemail listener keeps. They are moved to a backup folder, not deleted.
DATA_FILES = ['voicemail_messages.json', 'voicemail_items.json', 'voicemail_trips.json',
              'transcripts.txt', 'transcripts_original.txt', 'processed.sqlite3',
              'lost_found_items.html', 'voicemails.html', 'voicemail_messages.html']


def decode(value):
    try:
        return str(make_header(decode_header(value or '')))
    except Exception:
        return str(value or '')


def move_old(root: Path) -> tuple[Path, list]:
    backup = root / 'Old_Backups' / ('fresh_' + datetime.now().strftime('%Y-%m-%d_%H-%M-%S'))
    moved = []
    for name in DATA_FILES:
        src = root / name
        if src.exists():
            backup.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(backup / name))
            moved.append(name)
    return backup, moved


def read_env_value(path: Path, key: str) -> str:
    try:
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line.startswith(key + '='):
                return line.split('=', 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ''


def mark_existing_as_done(root: Path, gmail: str, password: str, sender: str, limit: int = 300) -> int:
    """Remember every voicemail email that is in Gmail right now, so only newer ones are read."""
    db = sqlite3.connect(root / 'processed.sqlite3')
    db.execute('CREATE TABLE IF NOT EXISTS done (uid TEXT, filename TEXT, PRIMARY KEY(uid,filename))')
    count = 0
    mail = imaplib.IMAP4_SSL('imap.gmail.com', 993)
    try:
        mail.login(gmail, password)
        status, _ = mail.select('INBOX', readonly=True)
        if status != 'OK':
            raise RuntimeError('Could not open the Gmail inbox.')
        status, result = mail.uid('search', None, 'FROM', f'"{sender}"')
        if status != 'OK':
            raise RuntimeError('Gmail search failed.')
        for uid in result[0].split()[-limit:]:
            status, data = mail.uid('fetch', uid, '(BODY.PEEK[])')
            if status != 'OK':
                continue
            raw = next((p[1] for p in data if isinstance(p, tuple) and isinstance(p[1], bytes)), None)
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            for part in msg.walk():
                name = decode(part.get_filename())
                mime = part.get_content_type().lower()
                if not name or not (name.lower().endswith(('.mp3', '.wav', '.m4a', '.ogg', '.webm')) or mime.startswith('audio/')):
                    continue
                db.execute('INSERT OR IGNORE INTO done(uid,filename) VALUES (?,?)', (uid.decode('ascii'), name))
                count += 1
        db.commit()
    finally:
        try:
            mail.logout()
        except Exception:
            pass
        db.close()
    return count


def main():
    print('\n=== START FRESH (voicemail) ===')
    print('This clears every old voicemail from the dashboard.')
    print('Nothing is deleted - it is moved to the Old_Backups folder. Your Gmail is not changed.')
    print('From now on only NEW voicemails that arrive in your email will appear.\n')
    print('IMPORTANT: close the voicemail listener window first (the black window), then continue.')
    if input('Type YES and press Enter to continue: ').strip().upper() != 'YES':
        print('Cancelled. Nothing was changed.')
        return
    env = ROOT / '.env'
    gmail = os.getenv('GMAIL_ADDRESS', '').strip() or read_env_value(env, 'GMAIL_ADDRESS')
    password = (os.getenv('GMAIL_APP_PASSWORD', '') or read_env_value(env, 'GMAIL_APP_PASSWORD')).replace(' ', '').strip()
    sender = os.getenv('VOICEMAIL_SENDER', '').strip() or read_env_value(env, 'VOICEMAIL_SENDER') or 'voicemail@intermedia.com'
    if not gmail or not password:
        print('\nCould not find the Gmail address or password. Start this with START_FRESH.bat so your private file is loaded.')
        return
    backup, moved = move_old(ROOT)
    print('\nMoved to', backup if moved else '(nothing to move)')
    for name in moved:
        print('  -', name)
    try:
        n = mark_existing_as_done(ROOT, gmail, password, sender)
    except Exception as exc:
        print('\nThe old data was moved, but Gmail could not be checked:', exc)
        print('Do NOT start the listener yet - it would read the old emails again. Fix the password, then run this again.')
        return
    print(f'\nMarked {n} existing voicemail emails as already seen.')
    print('Done. Start the system as usual. Only new voicemails will come in.')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nStopped.')
    input('\nPress Enter to close this window.')
