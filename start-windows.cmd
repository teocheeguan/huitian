@echo off
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -B app.py start %*
) else (
  python -B app.py start %*
)
if errorlevel 1 pause
