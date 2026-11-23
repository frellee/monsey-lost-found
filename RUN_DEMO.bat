@echo off
REM Runs the dashboard with FAKE sample customers (demo_data). Needs Python 3 and Pillow: pip install pillow
cd /d "%~dp0photo_dashboard"
set MONSEY_LISTENER_FOLDER=%~dp0demo_data\listener
if "%MONSEY_GEMINI_API_KEY%"=="" (
  echo.
  echo No Gemini key set. The dashboard still opens, but photo recognition will not work.
  echo To enable it, run:  set MONSEY_GEMINI_API_KEY=your-own-key   and start this file from the same window.
  echo.
)
python trial_server.py
pause
