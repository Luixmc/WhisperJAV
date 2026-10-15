@echo off
rem Convierte videos .ts a .mp4 sin recodificar (ffmpeg -c copy): misma calidad,
rem marcas de tiempo corregidas y con indice para adelantar al instante.
rem Uso: arrastra archivos .ts o carpetas sobre este .bat, o ejecutalo y escribe la ruta.
rem El .mp4 queda junto al .ts con el mismo nombre. NO borra los .ts.
rem Si el .mp4 ya existe, se lo salta.

setlocal
chcp 65001 >nul

where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo No encuentro ffmpeg en el PATH.
    pause
    exit /b 1
)

set /a OK=0, FALLOS=0, SALTADOS=0

if not "%~1"=="" goto argumentos
set "RUTA="
set /p "RUTA=Arrastra aqui un .ts o una carpeta y pulsa Enter: "
if not defined RUTA goto fin
set "RUTA=%RUTA:"=%"
call :ruta "%RUTA%"
goto resumen

:argumentos
if "%~1"=="" goto resumen
call :ruta "%~1"
shift
goto argumentos

:ruta
if exist "%~1\" (
    for /r "%~1" %%F in (*.ts) do call :convertir "%%F"
) else (
    call :convertir "%~1"
)
exit /b

:convertir
if /i not "%~x1"==".ts" exit /b
if exist "%~dpn1.mp4" (
    echo [saltado] %~nx1 ^(ya existe el .mp4^)
    set /a SALTADOS+=1
    exit /b
)
echo.
echo [convirtiendo] %~nx1
ffmpeg -hide_banner -loglevel error -stats -fflags +genpts -i "%~1" -map 0:v -map 0:a? -c copy -movflags +faststart -n "%~dpn1.mp4"
if errorlevel 1 (
    echo [ERROR] %~nx1
    if exist "%~dpn1.mp4" del "%~dpn1.mp4"
    set /a FALLOS+=1
) else (
    set /a OK+=1
)
exit /b

:resumen
echo.
echo Listo: %OK% convertidos, %SALTADOS% saltados, %FALLOS% con error.

:fin
pause
