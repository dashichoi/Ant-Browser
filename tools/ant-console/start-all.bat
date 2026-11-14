@echo off
setlocal
chcp 65001 >nul

rem ---- where is Ant Browser ----
set "HERE=%~dp0"
set "ANT_DIR=%~1"
if "%ANT_DIR%"=="" (
  set "ANT_DIR=%HERE%..\..\..\..\AntBrowser"
  if not exist "%ANT_DIR%\ant-chrome.exe" set "ANT_DIR=C:\Cdoe\AntBrowser"
)
if not exist "%ANT_DIR%\ant-chrome.exe" (
  echo [x] Ant Browser not found.
  echo     Pass the folder as the first argument: start-all.bat "D:\Tools\AntBrowser"
  pause
  exit /b 1
)

rem ---- env for the console ----
set "ANT_PORT=19876"
set "ANT_APP_DIR=%ANT_DIR%"

echo Ant Browser : %ANT_DIR%
echo Console API : http://127.0.0.1:8800
echo.

rem ---- 1) Ant (must run with its own folder as CWD) ----
tasklist /FI "IMAGENAME eq ant-chrome.exe" 2>nul | find /I "ant-chrome.exe" >nul
if errorlevel 1 (
  echo Starting Ant Browser...
  start "" /D "%ANT_DIR%" "%ANT_DIR%\ant-chrome.exe"
  timeout /t 8 /nobreak >nul
) else (
  echo Ant Browser is already running.
)

rem ---- 2) console in its own window (keeps its own sandbox/permissions) ----
echo Starting ant-console...
start "ant-console" /D "%HERE%" cmd /k python server.py 8800

timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:8800
endlocal
