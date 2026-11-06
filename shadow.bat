@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0pyfind.bat"
if not defined PY goto nopython
set "PYTHONIOENCODING=utf-8"
if not exist logs mkdir logs
set "BOTS_LOG=logs\shadow.log"
call "%~dp0rotate_log.bat" "%BOTS_LOG%"
if /i "%~1"=="loop" goto loop
"%PY%" -u shadow.py %*
exit /b 0
:loop
powershell -NoProfile -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; & '%PY%' -u shadow.py loop 2>&1 | ForEach-Object { Write-Host $_; Add-Content -Path '%BOTS_LOG%' -Value $_ -Encoding UTF8 }; exit $LASTEXITCODE"
set "RC=%errorlevel%"
exit /b %RC%
:nopython
echo Python with MetaTrader5 not found.
exit /b 1
