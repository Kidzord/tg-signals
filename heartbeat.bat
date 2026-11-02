@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".env" copy ".env.example" ".env" >nul

call "%~dp0pyfind.bat"
if not defined PY goto nopython

rem daily TG heartbeat; output goes to logs\heartbeat.log
if not exist logs mkdir logs
echo === start %date% %time% === >> logs\heartbeat.log
"%PY%" -u heartbeat.py >> logs\heartbeat.log 2>&1
exit /b 0

:nopython
echo Python with MetaTrader5 not found (see bot.bat).
exit /b 1
