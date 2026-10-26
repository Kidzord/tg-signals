@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".env" copy ".env.example" ".env" >nul

rem Один процесс на символ — у каждого свой файл состояния bot_state_<СИМВОЛ>.json
rem GER40 у этого брокера называется DE40
rem Каждое окно само находит Python через bot.bat (общий поиск pyfind.bat)
for %%S in (XAUUSD AUDUSD USDCAD GBPUSD US500 EUSTX50 UK100 DE40 JPN225) do (
    start "KidzordFX %%S" cmd /k "set BOT_SYMBOL=%%S&& call bot.bat"
)
echo Запущено окон: 9. Каждое — отдельный инструмент, своё состояние.
echo Лишние можно закрыть, бот в остальных продолжит работать.
timeout /t 5 >nul