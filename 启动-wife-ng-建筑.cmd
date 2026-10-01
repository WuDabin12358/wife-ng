@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Wife-NG.ps1" -CreativeBuild %*
if errorlevel 1 pause
