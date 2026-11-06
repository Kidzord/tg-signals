@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".env" copy ".env.example" ".env" >nul

call "%~dp0pyfind.bat"
if not defined PY goto nopython

rem daily TG heartbeat; output goes to logs\heartbeat.log
if not exist logs mkdir logs
set "BOTS_LOG=logs\heartbeat.log"
call "%~dp0rotate_log.bat" "%BOTS_LOG%"
set "PYTHONIOENCODING=utf-8"
echo === start %date% %time% === >> "%BOTS_LOG%"
powershell -NoProfile -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; & '%PY%' -u heartbeat.py %* 2>&1 | ForEach-Object { Write-Host $_; Add-Content -Path '%BOTS_LOG%' -Value $_ -Encoding UTF8 }; exit $LASTEXITCODE"
set "RC=%errorlevel%"
exit /b %RC%

:nopython
echo Python with MetaTrader5 not found (see bot.bat).
exit /b 1
