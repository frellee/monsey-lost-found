@echo off
setlocal
cd /d "%~dp0"
title Monsey Trails - Gemini Online Photo Recognition
if not exist "gemini_api_key.txt" (
 echo Your Gemini key is missing. Start SETUP_GEMINI_KEY first.
 pause
 exit /b 1
)
for %%A in ("gemini_api_key.txt") do if %%~zA LSS 16 (
 echo Gemini key is missing or too short. Double-click SETUP_GEMINI_KEY first.
 pause
 exit /b 1
)
set "PYTHON_EXE="
if exist "%USERPROFILE%\Downloads\YiddishModel\yiddish_test_env\Scripts\python.exe" set "PYTHON_EXE=%USERPROFILE%\Downloads\YiddishModel\yiddish_test_env\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%USERPROFILE%\Downloads\Free_Intermedia_Voicemail_Listener_Windows\Free_Intermedia_Voicemail_Listener\.venv\Scripts\python.exe" set "PYTHON_EXE=%USERPROFILE%\Downloads\Free_Intermedia_Voicemail_Listener_Windows\Free_Intermedia_Voicemail_Listener\.venv\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%LOCALAPPDATA%\Python\pythoncore-3.13-64\python.exe" set "PYTHON_EXE=%LOCALAPPDATA%\Python\pythoncore-3.13-64\python.exe"
if not defined PYTHON_EXE set "PYTHON_EXE=python"
echo.
echo MONSEY TRAILS - GOOGLE GEMINI FOUND ITEMS
echo Your dashboard is local. The item photos ONLY go to Google for identification.
echo Google free-tier limits apply. No Ollama needed.
echo Keep this window open while using the dashboard.
echo.
"%PYTHON_EXE%" trial_server.py
if errorlevel 1 echo ERROR: Copy the last few lines or send a screenshot to ChatGPT.
pause
