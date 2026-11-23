@echo off
cd /d "%~dp0"
set "PYTHON_EXE="
if exist "%USERPROFILE%\Downloads\YiddishModel\yiddish_test_env\Scripts\python.exe" set "PYTHON_EXE=%USERPROFILE%\Downloads\YiddishModel\yiddish_test_env\Scripts\python.exe"
if not defined PYTHON_EXE if exist "%USERPROFILE%\Downloads\Free_Intermedia_Voicemail_Listener_Windows\Free_Intermedia_Voicemail_Listener\.venv\Scripts\python.exe" set "PYTHON_EXE=%USERPROFILE%\Downloads\Free_Intermedia_Voicemail_Listener_Windows\Free_Intermedia_Voicemail_Listener\.venv\Scripts\python.exe"
if not defined PYTHON_EXE set "PYTHON_EXE=python"
echo This fetches free, licensed example photos from Wikimedia Commons and builds a local practice library.
echo It may take several minutes or longer. No monthly fee.
echo Leave this window open, and keep your regular voicemail listener unchanged.
"%PYTHON_EXE%" learn_public_photos.py
if errorlevel 1 echo ERROR: Library was not finished. Please send ChatGPT the message above.
pause
