@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".env" copy ".env.example" ".env" >nul

call "%~dp0pyfind.bat"
if not defined PY goto nopython

rem Автопосты: план/итог дня/неделя/промо + напоминания о постах.
rem Это замена reminder_scheduler.py: один процесс вместо двух, токен из .env.
if not exist logs mkdir logs
set "PYTHONIOENCODING=utf-8"
echo === start %date% %time% === >> logs\autoposts.log
"%PY%" -u autoposts.py %* >> logs\autoposts.log 2>&1
exit /b 0

:nopython
echo Python with MetaTrader5 not found (see bot.bat).
exit /b 1
