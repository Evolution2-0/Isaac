@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Isaac API - Windows 11

chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if not exist ".venv\Scripts\python.exe" (
  echo [HINWEIS] Isaac ist noch nicht eingerichtet. Starte Setup-Skript...
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "install_windows11.ps1" -NoStart
  if not exist ".venv\Scripts\python.exe" (
    echo [FEHLER] Einrichtung fehlgeschlagen. Bitte install_windows11.ps1 manuell pruefen.
    pause
    goto :end
  )
)

call ".venv\Scripts\activate.bat"
echo Isaac Flask API: http://127.0.0.1:5000
".venv\Scripts\python.exe" -m flask --app app:app run --host 127.0.0.1 --port 5000
:end
