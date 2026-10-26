# Запуск всех компонентов бота
Write-Host "=== Запуск всех компонентов ===" -ForegroundColor Green

# 1. Админ-панель
Write-Host "Запуск админ-панели..." -ForegroundColor Yellow
Start-Process -FilePath "cmd.exe" -ArgumentList "/c admin.bat" -WindowStyle Minimized
Start-Sleep -Seconds 2

# 2. Premium-бот
Write-Host "Запуск premium-бота..." -ForegroundColor Yellow
Start-Process -FilePath "cmd.exe" -ArgumentList "/c subbot.bat" -WindowStyle Minimized
Start-Sleep -Seconds 2

# 3. 9 ботов для разных символов
Write-Host "Запуск 9 ботов для символов..." -ForegroundColor Yellow
$symbols = @("XAUUSD", "AUDUSD", "USDCAD", "GBPUSD", "US500", "EUSTX50", "UK100", "DE40", "JPN225")
foreach ($sym in $symbols) {
    $env:BOT_SYMBOL = $sym
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c bot.bat" -WindowStyle Minimized
    Write-Host "  Бот для $sym запущен"
    Start-Sleep -Seconds 1
}

# 4. Планировщик напоминаний
Write-Host "Запуск планировщика напоминаний..." -ForegroundColor Yellow
Start-Process -FilePath "C:\Users\admin\AppData\Local\Programs\AutoClaw2\resources\vendor\python\python.exe" -ArgumentList "reminder_scheduler.py" -WorkingDirectory "C:\Users\admin\AppData\Roaming\AutoClaw-oversea-official\accounts\9d1f1cba3bfcc67fb3852763bc6d46f6072d4145e495c0c43478753a85c968b4\projects\Default\tg-signals" -WindowStyle Minimized

Write-Host ""
Write-Host "=== Все компоненты запущены ===" -ForegroundColor Green
Write-Host "Админ-панель: http://localhost:8081" -ForegroundColor Cyan
Write-Host "Токен: -yk-JdTEO-LIpqT1mpoZgiHZb94RTs-EqUZS-sLy8bQ" -ForegroundColor Cyan
Write-Host ""
Write-Host "Запущено:"
Write-Host "  - Админ-панель (port 8081)"
Write-Host "  - Premium-бот"
Write-Host "  - 9 ботов для символов"
Write-Host "  - Планировщик напоминаний (09:00, 14:00, 19:00 МСК)"
