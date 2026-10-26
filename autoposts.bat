@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".env" copy ".env.example" ".env" >nul

call "%~dp0pyfind.bat"

if not defined PY goto nopython

echo Интерпретатор: %PY%
"%PY%" -u autoposts.py %*
echo.
echo Код выхода: %errorlevel%
if "%~1"=="" pause
exit /b 0

:nopython
echo.
echo Не найден Python. Проверь установку или запусти вручную.
pause
exit /b 1
