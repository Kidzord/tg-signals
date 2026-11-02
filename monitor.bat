@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".env" copy ".env.example" ".env" >nul

call "%~dp0pyfind.bat"
if not defined PY goto nopython

rem Мониторинг ботов и внешний dead-man switch.
if not exist logs mkdir logs
set "PYTHONIOENCODING=utf-8"
echo === start %date% %time% === >> logs\monitor.log
"%PY%" -u monitor.py %* >> logs\monitor.log 2>&1
exit /b 0

:nopython
echo Python with MetaTrader5 not found (see bot.bat).
exit /b 1
