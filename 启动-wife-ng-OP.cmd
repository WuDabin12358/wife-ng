@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Wife-NG.ps1" -OpMode %*
if errorlevel 1 (
  echo.
  echo Wife NG OP mode startup failed. Check the message above.
  pause
)
