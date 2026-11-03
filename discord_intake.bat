@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".env" copy ".env.example" ".env" >nul

call "%~dp0pyfind.bat"
if not defined PY goto nopython

rem Discord -> консенсус: слушает разрешённые каналы, скриншоты через OCR.
rem Без DISCORD_INTAKE_ENABLED=1 процесс завершится сразу после проверки.
if not exist logs mkdir logs
set "PYTHONIOENCODING=utf-8"
echo === start %date% %time% === >> logs\discord_intake.log
"%PY%" -u discord_intake.py %* >> logs\discord_intake.log 2>&1
exit /b 0

:nopython
echo Python with MetaTrader5 not found (see bot.bat).
exit /b 1
