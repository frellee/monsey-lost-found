# Monsey Trails Lost & Found

A small local system for a bus company's lost-and-found:

1. **Voicemail listener** (`listener.py`, `gemini_voicemail.py`) reads voicemail emails from Gmail (IMAP), sends the audio to the Gemini API to transcribe and translate (Yiddish/English) and to extract the lost items and the bus ride (bus number, time, from, to).
2. **Photo + dashboard** (`trial_server.py`, `dashboard.html`) is a local web app (Python standard library HTTP server + one vanilla-JS HTML file). Staff photograph found items (Gemini vision identifies item and color), and the dashboard automatically matches found items to lost reports, one-to-one, oldest report first.

## What is NOT in this repository (on purpose)
- API keys and passwords (read from environment variables / a private file outside the project)
- All customer data: names, phone numbers, voicemails, photos, transcripts, the saved records (`trial_data/`)

## Environment variables
- `MONSEY_GEMINI_API_KEY` - Gemini API key
- `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` - mailbox that receives voicemails
- `MONSEY_LISTENER_FOLDER` - folder holding the listener output files

## How to run (Windows, local)
`START_ALL_MONSEY_TRAILS.bat` starts the listener and the dashboard. The dashboard opens at a random local port on `127.0.0.1`.

## Main files
| File | Purpose |
|---|---|
| `dashboard.html` | The whole UI (Lost Reports, Found Items, Returned, Not Found, Didn't Pick Up, Trash; customer popup; Hebrew calendar) |
| `trial_server.py` | Local server and JSON API, saves records to `trial_data/found_records.json` |
| `item_parser.py` | Turns voicemail text into item rows |
| `gemini_vision.py` | Photo recognition through Gemini |
| `auto_vision.py`, `second_opinion.py`, `reference_gallery.py`, `catalogue_search.py`, `learn_public_photos.py` | Older local-model recognition helpers |
| `listener.py` | Gmail voicemail listener |
| `gemini_voicemail.py` | Gemini audio transcription + item/trip extraction |
| `yiddish_bridge.py`, `intermedia_dualtext.py`, `heimish_*` | Yiddish/English text helpers |

## Review notes
Please review: UI/UX, performance with a few hundred records, security (the dashboard currently has no login and is meant for one trusted computer), and the best low-cost hosting option. A planned next step is hosting it on a small always-on server with per-user logins.

## Notes for the reviewer
- `photo_dashboard/` is the dashboard + photo side; `voicemail_listener/` is the Gmail/Gemini voicemail side. On the real computer they live in two separate folders and share data through the folder named in `MONSEY_LISTENER_FOLDER`.
- `start_windows.bat` / `START_*.bat` are the Windows launchers. `START_ALL_SAFE.bat` (loads the private secrets file, not included) is intentionally left out.
- Photo recognition used in daily operation is `gemini_vision.py`. The other helpers (`auto_vision.py`, `second_opinion.py`, `reference_gallery.py`, `catalogue_search.py`, `learn_public_photos.py`) are the older local-model path; `requirements.txt` lists only the voicemail side (python-dotenv, faster-whisper, winotify); the dashboard side needs Pillow and, for the old local path, numpy/torch/transformers.
- Real keys, the Gmail App Password and all customer data are excluded on purpose.
