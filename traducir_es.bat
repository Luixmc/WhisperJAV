@echo off
rem Traduce al espanol videos o .srt en japones con LM Studio (y CrispASR para los videos).
rem Uso: arrastra videos, archivos .srt o carpetas sobre este .bat, o ejecutalo y escribe la ruta.
rem Transcribe los videos sin .srt, estudia la obra si no tiene ficha, se salta lo ya traducido,
rem mueve cada obra terminada (video, .srt y ficha) a output\NOMBRE_DEL_VIDEO (junto a la
rem carpeta input) y al final descarga el modelo de LM Studio.

setlocal
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

rem --- Ajustes -------------------------------------------------------------
set MODELO=gemma-4-12b-it-qat
set TONO=contextual
set LOTE=25
rem Pon aqui --rehacer (volver a traducir), --sin-estudio, --mantener-modelo,
rem --sin-javdb, --sin-mover (dejarlo todo junto al video), --salida "CARPETA"
rem o --apagar (apaga la PC al terminar; se cancela con: shutdown /a)
set EXTRA=
rem -------------------------------------------------------------------------

set "PY=%~dp0.venv\Scripts\python.exe"
if exist "%PY%" goto rutas
echo No encuentro el Python de WhisperJAV en %PY%
pause
exit /b 1

:rutas
if not "%~1"=="" goto con_argumentos
set "RUTA="
set /p "RUTA=Arrastra aqui un video, un .srt o una carpeta y pulsa Enter: "
if not defined RUTA goto fin
set "RUTA=%RUTA:"=%"
"%PY%" -m whisperjav.translate.ficha traducir "%RUTA%" --modelo %MODELO% --tono %TONO% --lote %LOTE% %EXTRA%
goto fin

:con_argumentos
"%PY%" -m whisperjav.translate.ficha traducir %* --modelo %MODELO% --tono %TONO% --lote %LOTE% %EXTRA%

:fin
echo.
pause
