@echo off
rem Thin Windows entry point -- all real logic lives in start_quillwarden.py
rem (shared with macOS via start_quillwarden.command) so both platforms stay
rem in sync automatically instead of drifting apart as two separate scripts.
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel% neq 0 (
  echo Quillwarden needs Python, which wasn't found on this computer.
  echo Install it from https://python.org -- during install, check the box
  echo that says "Add python.exe to PATH" -- then run this again.
  pause
  exit /b 1
)

python start_quillwarden.py

rem Keep the window open after the server stops (or if it errored out) so
rem any final message is readable instead of the window vanishing instantly.
pause
