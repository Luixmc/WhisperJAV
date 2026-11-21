@echo off
rem Abre la app de WhisperJAV (cola de traduccion y biblioteca de lo traducido).
rem Puedes arrastrar videos, .srt o carpetas sobre este .bat para encolarlos.
set "PYW=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%PYW%" (
    echo No encuentro el Python de WhisperJAV en %PYW%
    pause
    exit /b 1
)
start "" /min "%PYW%" -m whisperjav.translate.ficha.app %*
