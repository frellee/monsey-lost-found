# Run the demo (Windows or Mac/Linux)

The demo uses **fake** sample customers from `demo_data/listener`, so no real customer data or keys are needed.

1. Install Python 3 (https://www.python.org/downloads/) and Pillow: `pip install pillow`
2. Download this repository (green **Code** button, **Download ZIP**) and unzip it.
3. *(Optional, for photo recognition)* create your **own** free Gemini key at https://aistudio.google.com/api-keys and set it:
   - Windows (Command Prompt): `set MONSEY_GEMINI_API_KEY=your-key`
   - Mac/Linux: `export MONSEY_GEMINI_API_KEY=your-key`
4. Start it from the same window:
   - Windows: `RUN_DEMO.bat`
   - Mac/Linux: `cd photo_dashboard && MONSEY_LISTENER_FOLDER=../demo_data/listener python3 trial_server.py`
5. A browser opens at a local address (127.0.0.1). Try: Lost Reports → customer name → popup; Found Items → "+ Add Found Item" (photo or typed); matching moves a report to Found Items; one report is 45 days old so it shows under **Not Found**.

The Gmail voicemail listener (`voicemail_listener/`) needs a real mailbox and is not part of the demo.
