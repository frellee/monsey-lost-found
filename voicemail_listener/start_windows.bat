@echo off
cd /d "%~dp0"
if not exist ".env" (
  echo FIRST: Copy .env.example to .env and fill in Gmail address and App Password.
  echo Read START_HERE.txt for instructions.
  pause
  exit /b 1
)
where py >nul 2>&1
if errorlevel 1 (
  echo Python was not found. Install Python from python.org and enable Add Python to PATH.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
   py -3.13 -m venv .venv
  if errorlevel 1 goto error
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto error
)
".venv\Scripts\python.exe" listener.py
if errorlevel 1 goto error
pause
exit /b 0
:error
echo Setup or listener failed. Read the message above.
pause
