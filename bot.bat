@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".env" copy ".env.example" ".env" >nul

call "%~dp0pyfind.bat"

if not defined PY goto nopython

echo Интерпретатор: %PY%
"%PY%" -u bot.py %*
echo.
echo Код выхода: %errorlevel%
if "%~1"=="" pause
exit /b 0

:nopython
echo.
echo Не найден Python с MetaTrader5.
echo Быстрая проверка - открой PowerShell в этой папке и выполни:
echo     python -c "import sys; print(sys.version)"
echo     dir "%APPDATA%\AutoClaw-oversea-official\toolchain\python"
echo.
pause
exit /b 1
