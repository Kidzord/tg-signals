@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".env" copy ".env.example" ".env" >nul

call "%~dp0pyfind.bat"

if not defined PY goto nopython

echo Интерпретатор: %PY%

rem файловый лог: вывод идёт и в окно, и в logs\bot_<символ>[_<тег>].log
set "BOTS_LOGTAG=%BOT_SYMBOL%"
if "%BOTS_LOGTAG%"=="" set "BOTS_LOGTAG=default"
if not "%~1"=="" set "BOTS_LOGTAG=%BOTS_LOGTAG%_%~1"
if not exist logs mkdir logs
set "BOTS_LOG=logs\bot_%BOTS_LOGTAG%.log"
call "%~dp0rotate_log.bat" "%BOTS_LOG%"
echo === старт %date% %time% === >> "%BOTS_LOG%"
set "PYTHONIOENCODING=utf-8"
powershell -NoProfile -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; & '%PY%' -u bot.py %* 2>&1 | ForEach-Object { Write-Host $_; Add-Content -Path '%BOTS_LOG%' -Value $_ -Encoding UTF8 }; exit $LASTEXITCODE"
set "RC=%errorlevel%"
echo.
echo Код выхода: %RC%
if not defined BOT_NO_PAUSE if "%~1"=="" pause
exit /b %RC%

:nopython
echo.
echo Не найден Python с MetaTrader5.
echo Быстрая проверка - открой PowerShell в этой папке и выполни:
echo     python -c "import sys; print(sys.version)"
echo     dir "%APPDATA%\AutoClaw-oversea-official\toolchain\python"
echo.
if not defined BOT_NO_PAUSE pause
exit /b 1
