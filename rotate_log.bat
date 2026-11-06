@echo off
rem rotate_log.bat <file> [max_mb] — ротация до старта процесса.
setlocal
set "F=%~1"
set "MAXMB=%~2"
if "%MAXMB%"=="" set "MAXMB=20"
if not exist "%F%" exit /b 0
for %%A in ("%F%") do set "SZ=%%~zA"
set /a LIMIT=%MAXMB%*1024*1024
if %SZ% LSS %LIMIT% exit /b 0
if exist "%F%.1" del /q "%F%.1" >nul 2>&1
move /y "%F%" "%F%.1" >nul 2>&1
exit /b 0
