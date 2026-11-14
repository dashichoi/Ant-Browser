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

where python >nul 2>&1
if errorlevel 1 (
  echo [x] Python 3 not found on PATH.
  pause
  exit /b 1
)

rem ---- 0) 先清场：双击即全新启动，不沿用上一个进程和窗口 ----
echo Stopping previous Ant Browser...
taskkill /F /IM ant-chrome.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo Stopping previous Ant Console...
python "%HERE%restart-console.py"
if errorlevel 1 (
  pause
  exit /b 1
)
timeout /t 1 /nobreak >nul

rem ---- 1) Ant (must run with its own folder as CWD) ----
echo Starting Ant Browser...
start "" /D "%ANT_DIR%" "%ANT_DIR%\ant-chrome.exe"
timeout /t 8 /nobreak >nul

rem ---- 2) console in its own window ----
echo Starting ant-console...
start "ant-console" /D "%HERE%" cmd /d /c python "%HERE%server.py" 8800

python "%HERE%restart-console.py" --wait
if errorlevel 1 (
  pause
  exit /b 1
)
start "" http://127.0.0.1:8800
endlocal
