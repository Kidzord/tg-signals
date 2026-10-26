@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".env" copy ".env.example" ".env" >nul

call "%~dp0pyfind.bat"
if not defined PY goto nopython

echo Запуск сервиса сигналов на %PY%...
"%PY%" -u app.py %*
echo.
echo Код выхода: %errorlevel%
if "%~1"=="" pause
exit /b 0

:nopython
echo.
echo Не найден Python с MetaTrader5. Смотри раздел nopython в bot.bat.
echo.
pause
exit /b 1
