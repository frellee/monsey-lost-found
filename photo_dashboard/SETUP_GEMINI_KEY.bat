@echo off
setlocal
cd /d "%~dp0"
title Set up Monsey Trails Gemini API key
if not exist "gemini_api_key.txt" type nul > "gemini_api_key.txt"
echo.
echo A browser will open to the official Google AI Studio API Keys page.
echo Choose a FREE-tier project and do NOT enable billing.
echo Create an API key, copy it, paste ONLY the key into Notepad, then press Ctrl+S.
echo Close Notepad when finished. Never share the key in ChatGPT.
echo.
start "" "https://aistudio.google.com/api-keys"
notepad "gemini_api_key.txt"
echo.
echo Next: double-click START_GEMINI_PHOTOS in this folder.
pause
