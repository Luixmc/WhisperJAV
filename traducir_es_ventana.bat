@echo off
rem Abre la ventana para traducir al espanol videos o .srt en japones.
rem Tambien puedes arrastrar videos, .srt o carpetas sobre este .bat.
set "PYW=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%PYW%" (
    echo No encuentro el Python de WhisperJAV en %PYW%
    pause
    exit /b 1
)
start "" "%PYW%" -m whisperjav.translate.ficha.ventana %*
