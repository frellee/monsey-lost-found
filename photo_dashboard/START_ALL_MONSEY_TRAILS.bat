@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
title Monsey Trails - Start Voicemail, Yiddish and Photos

echo.
echo ============================================================
echo  MONSEY TRAILS - ONE-CLICK START
echo  Free Intermedia Voicemail + Yiddish + Gemini Photo Matches
echo ============================================================
echo.
echo This starts your EXISTING programs. It does not install or
echo replace anything, change passwords, or delete saved messages.
echo.

rem Prefer the folder where this launcher was placed, or the known V3 photo folder.
set "PHOTO=%~dp0"
if not exist "%PHOTO%START_GEMINI_PHOTOS.bat" set "PHOTO=%USERPROFILE%\Downloads\MONSEY_TRAILS_PHOTO_AI_FIX_V3\Monsey_Trails_Photo_AI_Trial_V3"

rem Prefer the known active Intermedia installation; fall back to earlier layout.
set "LISTENER=%USERPROFILE%\Downloads\Free_Intermedia_Voicemail_Listener_Windows\Free_Intermedia_Voicemail_Listener"
if not exist "%LISTENER%\listener.py" set "LISTENER=%USERPROFILE%\Downloads\Free_Intermedia_Voicemail_Listener"
if not exist "%LISTENER%\listener.py" set "LISTENER=%USERPROFILE%\Downloads\Free_Intermedia_Voicemail_Listener_Windows"

if not exist "%PHOTO%\trial_server.py" (
  echo ERROR: Could not find the current Monsey Trails Photos program.
  echo Put THIS file in the folder containing START_GEMINI_PHOTOS.bat
  echo and trial_server.py, then run it again.
  echo Nothing was started or changed.
  goto :fail
)
if not exist "%PHOTO%\START_GEMINI_PHOTOS.bat" (
  echo ERROR: START_GEMINI_PHOTOS.bat was not found in the photo folder.
  echo Nothing was started or changed.
  goto :fail
)
if not exist "%LISTENER%\listener.py" (
  echo ERROR: Free Intermedia listener.py was not found.
  echo Expected it in your Downloads\Free_Intermedia_Voicemail_Listener_Windows folder.
  echo Nothing was started or changed.
  goto :fail
)
if not exist "%LISTENER%\start_windows.bat" (
  echo ERROR: The original Intermedia start_windows.bat was not found.
  echo We will NOT guess another command or modify your listener.
  echo Nothing was started or changed.
  goto :fail
)

rem The Yiddish speech model is integrated inside the existing Intermedia listener.
rem We do not start a duplicate standalone transcription process.
set "YIDDISH_MODEL=%USERPROFILE%\Downloads\YiddishModel\yiddish_test_env\Scripts\python.exe"
if not exist "%LISTENER%\yiddish_bridge.py" (
  echo WARNING: yiddish_bridge.py is not installed in the listener folder.
  echo Voicemail may run, but Yiddish recognition is NOT confirmed.
  echo Please send ChatGPT a screenshot of this window before continuing.
  goto :fail
)
if not exist "%YIDDISH_MODEL%" (
  echo WARNING: The locally installed Yiddish Python environment was not found.
  echo Please check Downloads\YiddishModel before running all components.
  goto :fail
)
findstr /C:"from yiddish_bridge import transcribe_en_yiddish" "%LISTENER%\listener.py" >nul
if errorlevel 1 (
  echo WARNING: Your listener does not appear connected to yiddish_bridge.py.
  echo No programs were started. Send ChatGPT this message.
  goto :fail
)

rem The photo dashboard already supports this setting and reads the listener's
rem voicemail_messages.json/transcripts.txt in its existing local-only server.
set "MONSEY_LISTENER_FOLDER=%LISTENER%"

echo [OK] Existing Intermedia voicemail listener found.
echo [OK] Yiddish transcription bridge and Python environment found.
echo [OK] Existing Gemini Photos dashboard found.
echo [OK] Photo dashboard will read local voicemail reports for matches.
echo.
echo Starting Intermedia in its own window...
start "Monsey Trails - Free Intermedia and Yiddish" /D "%LISTENER%" "%LISTENER%\start_windows.bat"
if errorlevel 1 (
  echo ERROR: Windows could not open the Intermedia start file.
  goto :fail
)

timeout /t 3 /nobreak >nul
echo Starting Gemini Photos and Possible Matches in its own window...
start "Monsey Trails - Photos and Possible Matches" /D "%PHOTO%" "%PHOTO%\START_GEMINI_PHOTOS.bat"
if errorlevel 1 (
  echo ERROR: Windows could not open the Gemini Photos start file.
  goto :fail
)

echo.
echo Two black windows should be running now.
echo Yiddish works INSIDE the Intermedia window; no third window needed.
echo The photo dashboard should open in your web browser.
echo Open Possible Matches to compare found items with lost reports.
echo Keep both program windows open while working.
echo.
echo NOTE: Starting the programs does not confirm Gmail access,
echo the accuracy of Yiddish transcription, or live Gemini quota.
echo Check both program windows for any errors.
echo.
echo Press any key to close ONLY this launcher window.
pause >nul
exit /b 0

:fail
echo.
echo Nothing was deleted. No files or credentials were changed.
echo Press any key after reading the message above.
pause >nul
exit /b 1
