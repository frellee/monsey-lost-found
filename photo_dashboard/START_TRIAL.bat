@echo off
setlocal
cd /d "%~dp0"
title Monsey Trails - Free Photo AI Trial V2
set "HF_HUB_DISABLE_TELEMETRY=1"
echo.
echo MONSEY TRAILS - FOUND ITEM PHOTO AI V2
 echo Free, local, no subscription, no paid AI service.
echo You only need to upload a picture. The item and color are suggested automatically. This version improves phone recognition and color-text styling.
echo First photo needs a free AI model download (about 600 MB) and can take a while.
echo Keep the black window open while using the dashboard.
echo.
set "PYTHON_EXE="
if exist "%USERPROFILE%\Downloads\YiddishModel\yiddish_test_env\Scripts\python.exe" set "PYTHON_EXE=%USERPROFILE%\Downloads\YiddishModel\yiddish_test_env\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%USERPROFILE%\Downloads\Free_Intermedia_Voicemail_Listener_Windows\Free_Intermedia_Voicemail_Listener\.venv\Scripts\python.exe" set "PYTHON_EXE=%USERPROFILE%\Downloads\Free_Intermedia_Voicemail_Listener_Windows\Free_Intermedia_Voicemail_Listener\.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%LOCALAPPDATA%\Python\pythoncore-3.13-64\python.exe" set "PYTHON_EXE=%LOCALAPPDATA%\Python\pythoncore-3.13-64\python.exe"
if not defined PYTHON_EXE (
 echo Could not find Python. Send ChatGPT a screenshot of this message.
 pause
 exit /b 1
)
echo Using your existing Python environment.
"%PYTHON_EXE%" -c "import torch, transformers, PIL, safetensors, numpy" >nul 2>nul
if errorlevel 1 (
 echo Preparing free local photo-recognition libraries. This may take several minutes...
 "%PYTHON_EXE%" -m pip install --disable-pip-version-check pillow numpy safetensors "transformers>=4.45,<5" torch
 if errorlevel 1 (
  echo.
  echo ERROR: Could not install free image-recognition libraries.
  echo Check internet connection, then try START_TRIAL again.
  echo Your existing Intermedia listener and saved messages are unchanged.
  pause
  exit /b 1
 )
)
echo.
echo Starting the photo dashboard... Please wait.
"%PYTHON_EXE%" trial_server.py
if errorlevel 1 echo The trial stopped with an error. Send ChatGPT a screenshot.
echo Press any key to close this window.
pause >nul
