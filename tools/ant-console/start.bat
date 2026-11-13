@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3 not found. Install it and make sure "python" is on PATH.
  pause
  exit /b 1
)
python server.py %1
pause
