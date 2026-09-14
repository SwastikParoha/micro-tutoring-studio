@echo off
title Micro-Tutoring Studio
cd /d "%~dp0"

echo.
echo   Starting Micro-Tutoring Studio...
echo   Your browser will open in a few seconds.
echo.
echo   KEEP THIS WINDOW OPEN while you use the app.
echo   Close it (or press Ctrl+C) when you are finished.
echo.

python main.py serve

echo.
echo   The server has stopped.
pause
