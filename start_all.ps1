# Запуск всех компонентов бота. Повторный запуск безопасен: уже работающие
# компоненты не перезапускаются (защита от дублей при логоне и вручную).
$PY   = "C:\Users\admin\AppData\Local\Programs\AutoClaw2\resources\vendor\python\python.exe"
$DIR  = "C:\Users\admin\Downloads\tg-signals"
# userbase с numpy/MetaTrader5/dotenv — без него у python-запусков нет пакетов
$env:PYTHONUSERBASE = "C:\Users\admin\AppData\Roaming\AutoClaw-oversea-official\toolchain\python"

Write-Host "=== Запуск всех компонентов ===" -ForegroundColor Green

if (-not (Test-Path "$DIR\logs")) { New-Item -ItemType Directory -Path "$DIR\logs" | Out-Null }

# Защита от гонки: watchdog может вызвать start_all, пока ручной запуск ещё идёт.
$LOCK = "$DIR\logs\start_all.lock"
if (Test-Path $LOCK) {
    $age = (Get-Date) - (Get-Item $LOCK).LastWriteTime
    if ($age.TotalSeconds -lt 120) {
        Write-Host "start_all уже выполняется — выхожу" -ForegroundColor DarkGray
        exit 0
    }
}
Set-Content -Path $LOCK -Value (Get-Date -Format o) -Encoding UTF8

#--- что уже работает (по командным строкам python.exe)
$cmdlines = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
              ForEach-Object { $_.CommandLine })
function Test-Running([string]$pattern) {
    @($cmdlines | Where-Object { $_ -match $pattern }).Count -gt 0
}

# Живой ли конкретный bot.py: heartbeat пишет свой PID, проверяем именно его.
# Это защищает от дублей, когда команда запуска у всех bot.py одинаковая.
function Test-BotAlive([string]$sym, [string]$tag) {
    $hb = Join-Path $DIR "hb_$($sym)_$tag.json"
    if (-not (Test-Path $hb)) { return $false }
    try {
        $data = Get-Content $hb -Raw | ConvertFrom-Json
        $botPid = [int]$data.pid
        if ($data.symbol -and $data.symbol -ne $sym) { return $false }
    } catch { return $false }
    if (-not $botPid) { return $false }
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$botPid" -ErrorAction SilentlyContinue
    if (-not $proc) { return $false }
    if ($proc.CommandLine -notmatch '(?<![a-z])bot\.py') { return $false }
    return $true
}

#--- 1. Premium-бот (лог в logs\subbot.log)
if (Test-Running 'subbot\.py') {
    Write-Host "Premium-бот уже работает — пропускаю" -ForegroundColor DarkGray
} else {
    Write-Host "Запуск premium-бота..." -ForegroundColor Yellow
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c subbot.bat" -WorkingDirectory $DIR -WindowStyle Minimized
}
Start-Sleep -Seconds 2

#--- 2. Range-боты: только символы, прошедшие walk-forward (проверено 2026-11-02,
#    tools/walk_forward.py, XAUUSD...JPN225): AUDUSD +8.65R net (8/13 фолдов),
#    USDCAD +19.1R net (7/13, лучшая средняя +0.47R). Отключены как непрошедшие:
#    GBPUSD -14.9R, JPN225 -12.6R, XAUUSD -9.0R (на золоте теперь classic),
#    DE40 -5.8R, US500 +0.45R (ноль), EUSTX50 -1.1R (ноль). Вернуть — добавить в список.
$symbols = @("AUDUSD", "USDCAD")
Write-Host "Запуск range-ботов: $($symbols -join ', ')..." -ForegroundColor Yellow
foreach ($sym in $symbols) {
    if (Test-BotAlive $sym "range") {
        Write-Host "  Range-бот $sym уже работает — пропускаю" -ForegroundColor DarkGray
        continue
    }
    $env:BOT_SYMBOL = $sym
    $env:STRATEGY = "range"
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c bot.bat" -WorkingDirectory $DIR -WindowStyle Minimized
    Write-Host "  Range-бот $sym запущен"
    Start-Sleep -Seconds 1
}

#--- 3. Classic-стратегия на золоте (прошла walk-forward: +8.6R net, 5/8 фолдов)
$env:BOT_SYMBOL = "XAUUSD"
$env:STRATEGY = "classic"
if (Test-BotAlive "XAUUSD" "classic") {
    Write-Host "Classic-бот уже работает — пропускаю" -ForegroundColor DarkGray
} else {
    Write-Host "Запуск classic-бота XAUUSD..." -ForegroundColor Yellow
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c bot.bat classic" -WorkingDirectory $DIR -WindowStyle Minimized
}
$env:STRATEGY = "range"

#--- 4. Автопосты + напоминания (лог в logs\autoposts.log)
#    Заменяет reminder_scheduler.py: раньше в нём был захардкожен токен,
#    и он дублировал напоминания autoposts.py.
$old = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
         Where-Object { $_.CommandLine -match 'reminder_scheduler\.py' })
if ($old.Count -gt 0) {
    $old | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Write-Host "Остановлен устаревший reminder_scheduler.py" -ForegroundColor DarkYellow
}
if (Test-Running 'autoposts\.py') {
    Write-Host "Автопосты уже работают — пропускаю" -ForegroundColor DarkGray
} else {
    Write-Host "Запуск автопостов и напоминаний..." -ForegroundColor Yellow
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c scheduler.bat" -WorkingDirectory $DIR -WindowStyle Minimized
}

#--- 5. Heartbeat: ежедневный отчёт в Telegram (лог в logs\heartbeat.log)
if (Test-Running 'heartbeat\.py') {
    Write-Host "Heartbeat уже работает — пропускаю" -ForegroundColor DarkGray
} else {
    Write-Host "Запуск heartbeat (отчёт в Telegram)..." -ForegroundColor Yellow
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c heartbeat.bat" -WorkingDirectory $DIR -WindowStyle Minimized
}

#--- 6. Мониторинг: пульс ботов + внешний dead-man switch (logs\monitor.log)
if (Test-Running 'monitor\.py') {
    Write-Host "Мониторинг уже работает — пропускаю" -ForegroundColor DarkGray
} else {
    Write-Host "Запуск мониторинга..." -ForegroundColor Yellow
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c monitor.bat" -WorkingDirectory $DIR -WindowStyle Minimized
}

#--- 7. Watchdog: сам поднимает упавшие компоненты каждые 5 минут
$wd = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'watchdog\.ps1' })
if ($wd.Count -gt 0) {
    Write-Host "Watchdog уже работает — пропускаю" -ForegroundColor DarkGray
} else {
    Write-Host "Запуск watchdog..." -ForegroundColor Yellow
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$DIR\watchdog.ps1`"" `
        -WorkingDirectory $DIR -WindowStyle Hidden
}

Remove-Item $LOCK -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "=== Все компоненты запущены ===" -ForegroundColor Green
Write-Host "Запущено:"
Write-Host "  - Premium-бот (logs\subbot.log)"
Write-Host "  - Range-боты: $($symbols -join ', ') (logs\bot_<символ>.log)"
Write-Host "  - Classic-бот XAUUSD (logs\bot_XAUUSD_classic.log)"
Write-Host "  - Автопосты: план/итоги/промо + напоминания (logs\autoposts.log)"
Write-Host "  - Heartbeat в Telegram (09:00 МСК; logs\heartbeat.log)"
Write-Host "  - Мониторинг ботов и доставки (logs\monitor.log)"
Write-Host "  - Watchdog: автоперезапуск каждые 5 минут (logs\watchdog.log)"
