# Запуск всех 9 ботов для разных символов
$symbols = @("XAUUSD", "AUDUSD", "USDCAD", "GBPUSD", "US500", "EUSTX50", "UK100", "DE40", "JPN225")

foreach ($sym in $symbols) {
    $env:BOT_SYMBOL = $sym
    Start-Process -FilePath "cmd" -ArgumentList "/c", "bot.bat" -WindowStyle Minimized
    Write-Host "Запущен бот для $sym"
}

Write-Host "Запущено ботов: $($symbols.Count)"
