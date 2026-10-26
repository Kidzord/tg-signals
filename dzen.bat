@echo off
chcp 65001 >nul
cd /d "%~dp0"

call "%~dp0pyfind.bat"

if not defined PY goto nopython

echo Интерпретатор: %PY%
"%PY%" -u ru_post.py dzen %*
echo.
echo Код выхода: %errorlevel%
pause
exit /b 0

:nopython
echo.
echo Не найден Python. Проверь установку или запусти вручную.
pause
exit /b 1
