@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Wife-NG.ps1" %*
if errorlevel 1 (
  echo.
  echo Wife NG 启动失败，请查看上方信息。
  pause
)
