@echo off
rem Общий поиск Python с установленным MetaTrader5.
rem Вызов из другого bat: call pyfind.bat  ->  путь к интерпретатору кладётся в PY
rem (PY пустой, если рабочий интерпретатор не найден).
chcp 65001 >nul

rem Пакеты (MetaTrader5, matplotlib) лежат в пользовательской папке AutoClaw.
rem Без PYTHONUSERBASE Python их не видит — задаём явно.
if not defined PYTHONUSERBASE if exist "%APPDATA%\AutoClaw-oversea-official\toolchain\python" set "PYTHONUSERBASE=%APPDATA%\AutoClaw-oversea-official\toolchain\python"
if not defined PYTHONUSERBASE if exist "C:\Users\admin\AppData\Roaming\AutoClaw-oversea-official\toolchain\python" set "PYTHONUSERBASE=C:\Users\admin\AppData\Roaming\AutoClaw-oversea-official\toolchain\python"

set "PY="

rem 0) явно заданный интерпретатор — только если он рабочий
if not defined SIGNAL_PYTHON goto try_path
"%SIGNAL_PYTHON%" -c "import MetaTrader5" >nul 2>&1
if not errorlevel 1 set "PY=%SIGNAL_PYTHON%"
if defined PY exit /b 0

:try_path
rem 1) python из PATH
python -c "import MetaTrader5" >nul 2>&1
if not errorlevel 1 (
    set "PY=python"
    exit /b 0
)

rem 2) интерпретатор AutoClaw
for %%P in (
    "%LOCALAPPDATA%\Programs\AutoClaw2\resources\vendor\python\python.exe"
    "%USERPROFILE%\AppData\Local\Programs\AutoClaw2\resources\vendor\python\python.exe"
    "C:\Users\admin\AppData\Local\Programs\AutoClaw2\resources\vendor\python\python.exe"
) do (
    if not defined PY if exist %%P (
        %%P -c "import MetaTrader5" >nul 2>&1
        if not errorlevel 1 set "PY=%%P"
    )
)
exit /b 0
