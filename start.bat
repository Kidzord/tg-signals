@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"

if not exist ".env" copy ".env.example" ".env" >nul

rem общий поиск Python с MetaTrader5 -> переменная PY
call "%~dp0pyfind.bat"

if not exist "bin\cloudflared.exe" (
    echo Не найден bin\cloudflared.exe — туннель запустить нечем.
    pause
    exit /b 1
)

echo [1/3] Запускаю сервис сигналов в отдельном окне...
start "KidzordFX сервис" cmd /k "call run.bat"
timeout /t 5 >nul

echo [2/3] Запускаю туннель Cloudflare...
del tunnel.log >nul 2>&1
del url.txt >nul 2>&1
start "KidzordFX туннель" /min cmd /c "bin\cloudflared.exe tunnel --url http://localhost:8080 --no-autoupdate > tunnel.log 2>&1"

echo [3/3] Жду адрес от Cloudflare, до 20 секунд...
timeout /t 14 >nul

powershell -NoProfile -Command "$t=''; if(Test-Path 'tunnel.log'){$t=Get-Content 'tunnel.log' -Raw}; $m=[regex]::Match([string]$t,'https://[a-z0-9-]+\.trycloudflare\.com'); $u=''; if($m.Success){$u=$m.Value}; Set-Content -Path 'url.txt' -Value $u -Encoding ascii"
set /p URL=<url.txt

echo.
if defined URL (
    echo Туннель поднят:  !URL!
    echo.
    echo Адрес для TradingView ^(скопируй целиком^):
    echo ------------------------------------------------------------
    "!PY!" -u app.py url !URL!
    echo ------------------------------------------------------------
) else (
    echo Адрес получить не удалось.
    echo Открой окно "KidzordFX туннель" на панели задач и посмотри, что там.
    echo Если Cloudflare ругается — запусти файл ещё раз.
)
echo.
echo Это окно можно закрыть. Окна "сервис" и "туннель" должны остаться открытыми.
pause