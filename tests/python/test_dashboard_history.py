import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from time import perf_counter
from urllib.request import urlopen
from unittest.mock import patch


DASHBOARD_DIR = Path(__file__).resolve().parents[2] / 'photo_dashboard'
with patch.object(sys, 'path', [str(DASHBOARD_DIR), *sys.path]):
    import trial_server


class DashboardHistoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        paths = patch.multiple(trial_server, LISTENER=self.root,
                               STATE=self.root / 'found_records.json')
        paths.start()
        self.addCleanup(paths.stop)

    def test_api_state_returns_all_4000_voicemails(self):
        transcripts = [
            {'date': '2025-02-08 10:00 AM', 'filename': f'demo_{number:04}.mp3',
             'transcript': 'I lost a black hat on bus 12.', 'flagged': True}
            for number in range(4000)
        ]
        (self.root / 'voicemail_messages.json').write_text(json.dumps(transcripts), encoding='utf-8')

        with trial_server.ThreadingHTTPServer(('127.0.0.1', 0), trial_server.Handler) as server:
            worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01})
            worker.start()
            try:
                start = perf_counter()
                with urlopen(f'http://127.0.0.1:{server.server_port}/api/state', timeout=10) as response:
                    body = response.read()
                    elapsed = perf_counter() - start
                    self.assertEqual(response.status, 200)
                    self.assertEqual(int(response.headers['Content-Length']), len(body))
            finally:
                server.shutdown()
                worker.join()

        state = json.loads(body)
        self.assertEqual(state['voicemails_seen'], len(transcripts))
        self.assertEqual(len(state['lost']), len(transcripts))
        self.assertEqual({report['filename'] for report in state['lost']},
                         {transcript['filename'] for transcript in transcripts})
        print(f'/api/state with 4,000 voicemails took {elapsed:.3f} s, {len(body):,} bytes')

    def test_legacy_transcript_history_returns_all_entries_newest_first(self):
        history = '\n'.join(
            f'--- 2025-02-08 10:00 AM ---\nAttachment: demo_{number:04}.mp3\n'
            'Transcript: I lost a black hat.\n' for number in range(605)
        )
        (self.root / 'transcripts.txt').write_text(history, encoding='utf-8')

        entries = trial_server.load_entries()
        self.assertEqual([entry['filename'] for entry in entries],
                         [f'demo_{number:04}.mp3' for number in reversed(range(605))])
