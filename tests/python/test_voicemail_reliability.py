import importlib
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch


LISTENER_DIR = Path(__file__).resolve().parents[2] / 'voicemail_listener'
# Mailbox settings are irrelevant to these tests. Avoid loading an office .env.
with patch.object(sys, 'path', [str(LISTENER_DIR), *sys.path]):
    with patch.dict(sys.modules, dotenv=SimpleNamespace(load_dotenv=Mock())):
        listener = importlib.import_module('listener')
    from intermedia_dualtext import record_and_render, refresh_existing_dashboard


class VoicemailHistoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.json_path = self.root / 'voicemail_messages.json'
        self.page_path = self.root / 'voicemail_messages.html'

    def save_report(self, number, **kwargs):
        record_and_render(self.root, '2025-02-08 10:00 AM', 'Demo voicemail',
                          f'demo_{number:04}.mp3', 'I lost a black hat.', True, 'en',
                          **kwargs)

    def test_json_keeps_every_record_and_html_shows_the_newest_500(self):
        for number in range(605):
            self.save_report(number)

        reports = json.loads(self.json_path.read_text(encoding='utf-8'))
        self.assertEqual([report['filename'] for report in reports],
                         [f'demo_{number:04}.mp3' for number in reversed(range(605))])
        for refresh in (False, True):
            if refresh:
                refresh_existing_dashboard(self.root)
            page = self.page_path.read_text(encoding='utf-8')
            self.assertEqual(page.count('<article class="card">'), 500)
            self.assertIn('demo_0105.mp3', page)
            self.assertNotIn('demo_0104.mp3', page)

    def test_invalid_history_is_not_overwritten(self):
        for existing in ('[{', '{"unexpected": true}'):
            with self.subTest(existing=existing):
                self.json_path.write_text(existing, encoding='utf-8')
                with self.assertRaises(ValueError):
                    self.save_report(1)
                self.assertEqual(self.json_path.read_text(encoding='utf-8'), existing)

    def test_retry_after_a_page_write_failure_keeps_one_json_report(self):
        with patch('intermedia_dualtext._render', side_effect=OSError):
            with self.assertRaises(OSError):
                self.save_report(1, uid='101')
        self.save_report(1, uid='101')
        self.save_report(2, uid='101')
        reports = json.loads(self.json_path.read_text(encoding='utf-8'))
        self.assertEqual([report['filename'] for report in reports],
                         ['demo_0002.mp3', 'demo_0001.mp3'])
        self.assertEqual(self.page_path.read_text(encoding='utf-8').count('<article'), 2)


class ListenerPollingTests(unittest.TestCase):
    def test_selection_sorts_large_backlog_and_skips_done_uids(self):
        uids = [str(number) for number in reversed(range(1, 43))]
        selected = listener.select_uids_to_process(uids, {'10', '40'}, False, 20)
        self.assertEqual(selected, [str(number) for number in range(1, 43)
                                    if number not in {10, 40}])

    def test_first_run_selects_only_the_newest_limit_in_processing_order(self):
        uids = [str(number) for number in reversed(range(1, 43))]
        self.assertEqual(listener.select_uids_to_process(uids, set(), True, 20),
                         [str(number) for number in range(23, 43)])

    def test_poll_uses_sender_and_since_and_limits_only_an_empty_done_table(self):
        mail = Mock()
        process = Mock()
        mail.uid.return_value = ('OK', [b'30 20 10 1'])
        with closing(sqlite3.connect(':memory:')) as db:
            listener.initialize_processing_db(db)
            with patch.multiple(listener, date=Mock(today=lambda: date(2025, 1, 8)),
                                LOOKBACK_DAYS=14, FIRST_RUN_LIMIT=2,
                                SENDER='voicemail@example.invalid',
                                process_uid=process):
                listener.poll_voicemails(mail, db)
                mail.uid.assert_called_once_with('search', None, 'FROM',
                                                 '"voicemail@example.invalid"',
                                                 'SINCE', '25-Dec-2024')
                self.assertEqual(process.call_args_list, [call(mail, db, '20'), call(mail, db, '30')])
                db.execute('INSERT INTO done VALUES (?, ?)', ('20', listener.UID_COMPLETE))
                db.commit()
                process.reset_mock()
                listener.poll_voicemails(mail, db)
                self.assertEqual(process.call_args_list,
                                 [call(mail, db, '1'), call(mail, db, '10'), call(mail, db, '30')])

    def test_legacy_done_rows_do_not_hide_unfinished_sibling_attachments(self):
        message = EmailMessage()
        message.set_content('Demo notification')
        message.add_attachment(b'demo audio A', 'audio', 'mpeg', filename='demo-a.mp3')
        message.add_attachment(b'demo audio B', 'audio', 'mpeg', filename='demo-b.mp3')
        message.add_attachment(b'demo audio C', 'audio', 'mpeg', filename='demo-c.mp3')
        mail = Mock()
        mail.uid.side_effect = lambda method, *args: (
            ('OK', [b'42']) if method == 'search' else ('OK', [(b'headers', message.as_bytes())])
        )
        recorded = []

        def save_attachment(db, uid, filename, *args):
            recorded.append(filename)
            if recorded == ['demo-b.mp3']:
                raise OSError('Demo failed write')
            db.execute('INSERT INTO done VALUES (?, ?)', (uid, filename))
            return True

        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE TABLE done (uid TEXT, filename TEXT, PRIMARY KEY (uid, filename))')
            db.execute('INSERT INTO done VALUES (?, ?)', ('42', 'demo-a.mp3'))
            db.commit()
            listener.initialize_processing_db(db)
            with patch.object(listener, 'process_attachment', side_effect=save_attachment):
                with self.assertLogs(listener.log, level='WARNING'):
                    listener.poll_voicemails(mail, db)
                self.assertEqual(recorded, ['demo-b.mp3', 'demo-c.mp3'])
                listener.poll_voicemails(mail, db)
                self.assertEqual(recorded, ['demo-b.mp3', 'demo-c.mp3', 'demo-b.mp3'])
                mail.reset_mock()
                listener.poll_voicemails(mail, db)
                self.assertEqual(mail.uid.call_count, 1)
                self.assertEqual(mail.uid.call_args.args[0], 'search')

    def test_gemini_retries_survive_restart_and_fall_back_without_logging_error_text(self):
        private_text = 'fake-api-secret and fake transcript'
        gemini = SimpleNamespace(transcribe_with_gemini=Mock(side_effect=RuntimeError(private_text)))
        transcript = ('Demo hat.', 'demo hat', 'en')
        whisper = SimpleNamespace(transcribe_en_yiddish=Mock(return_value=transcript))
        audio = Path('demo.mp3')
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(sys.modules, gemini_voicemail=gemini, yiddish_bridge=whisper), \
                patch.object(listener, 'ENGINE', 'gemini'), \
                patch.object(listener, 'get_whisper', return_value=Mock()) as local_model, \
                self.assertLogs(listener.log, level='WARNING') as logs:
            db_path = Path(tmp) / 'processed.sqlite3'
            for attempt in range(1, listener.GEMINI_MAX_TRIES + 2):
                with closing(sqlite3.connect(db_path)) as db:
                    listener.initialize_processing_db(db)
                    result = listener.transcribe_voicemail(audio, '42', 'demo.mp3', db)
                    expected = transcript if attempt >= listener.GEMINI_MAX_TRIES else None
                    self.assertEqual(result, expected)
                    count, error_type, updated_at = db.execute(
                        'SELECT count, last_error, updated_at FROM failures WHERE uid=? AND filename=?',
                        ('42', 'demo.mp3'),
                    ).fetchone()
                    self.assertEqual(count, min(attempt, listener.GEMINI_MAX_TRIES))
                    self.assertEqual(error_type, 'RuntimeError')
                    self.assertTrue(updated_at)

        self.assertEqual(gemini.transcribe_with_gemini.call_args_list,
                         [call(audio, listener.VOCABULARY_HINT)] * listener.GEMINI_MAX_TRIES)
        self.assertEqual(whisper.transcribe_en_yiddish.call_args_list,
                         [call(audio, local_model.return_value, listener.VOCABULARY_HINT)] * 2)
        self.assertNotIn(private_text, '\n'.join(logs.output))

    def test_successful_retry_saves_reports_and_clears_the_failure_count(self):
        message = EmailMessage()
        message['Subject'] = 'Demo voicemail'
        message.set_content('Voicemail transcript: Missing a black hat.')
        audio = b'demo audio'
        message.add_attachment(audio, 'audio', 'mpeg', filename='demo.mp3')
        mail = Mock()
        mail.uid.return_value = ('OK', [(b'headers', message.as_bytes())])
        gemini = SimpleNamespace()
        items = [{'item': 'Hat', 'color': 'black'}]
        trip = {'bus': '12'}

        def transcribe(path, hint):
            self.assertEqual(path.read_bytes(), audio)
            self.assertEqual(path.suffix, '.mp3')
            self.assertEqual(hint, listener.VOCABULARY_HINT)
            gemini.LAST_RESULT = {'items': items, 'trip': trip}
            return 'I lost a black hat.', 'I lost a black hat', 'en'

        gemini.transcribe_with_gemini = Mock(side_effect=transcribe)
        with tempfile.TemporaryDirectory() as tmp, closing(sqlite3.connect(':memory:')) as db, \
                patch.object(sys, 'path', [str(LISTENER_DIR), *sys.path]), \
                patch.dict(sys.modules, gemini_voicemail=gemini), \
                patch.multiple(listener, ROOT=Path(tmp), ENGINE='gemini', notify=Mock()):
            root = Path(tmp)
            listener.initialize_processing_db(db)
            listener.record_gemini_failure(db, '42', 'demo.mp3', 'RuntimeError')
            listener.process_uid(mail, db, '42')

            done = db.execute('SELECT filename FROM done WHERE uid=?', ('42',)).fetchall()
            self.assertEqual(set(done), {(listener.UID_COMPLETE,), ('demo.mp3',)})
            self.assertIsNone(db.execute('SELECT count FROM failures').fetchone())
            reports = json.loads((root / 'voicemail_messages.json').read_text(encoding='utf-8'))
            self.assertEqual(reports[0]['provider_transcript'], 'Missing a black hat.')
            self.assertEqual(reports[0]['transcript'], 'I lost a black hat.')
            self.assertEqual(reports[0]['original_audio_transcript'], 'I lost a black hat')
            self.assertEqual(json.loads((root / 'voicemail_items.json').read_text(encoding='utf-8')),
                             {'demo.mp3': items})
            self.assertEqual(json.loads((root / 'voicemail_trips.json').read_text(encoding='utf-8')),
                             {'demo.mp3': trip})
            self.assertTrue((root / 'lost_found_items.html').is_file())
            sent_audio_path = gemini.transcribe_with_gemini.call_args.args[0]
            self.assertFalse(sent_audio_path.exists())
