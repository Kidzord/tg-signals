# Watchdog: раз в 5 минут проверяет состав процессов и поднимает недостающие.
# start_all.ps1 идемпотентен — уже работающие компоненты он не перезапускает.
# Запускается из start_all.ps1 (секция 7) и дальше крутится сам.
$ErrorActionPreference = "Continue"
$DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$LOG = Join-Path $DIR "logs\watchdog.log"
if (-not (Test-Path (Join-Path $DIR "logs"))) {
    New-Item -ItemType Directory -Path (Join-Path $DIR "logs") | Out-Null
}
$interval = 300

# Даём ручному start_all завершить первый запуск, чтобы не плодить дубли.
Start-Sleep -Seconds 60

while ($true) {
    # Ротация своего лога: watchdog живёт месяцами, файл иначе растёт без предела.
    if ((Test-Path $LOG) -and ((Get-Item $LOG).Length -gt 20MB)) {
        Move-Item -Path $LOG -Destination "$LOG.1" -Force -ErrorAction SilentlyContinue
    }
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $LOG -Value "=== watchdog $stamp ===" -Encoding UTF8
    try {
        & (Join-Path $DIR "start_all.ps1") *>&1 |
            ForEach-Object { Add-Content -Path $LOG -Value $_ -Encoding UTF8 }
    } catch {
        Add-Content -Path $LOG -Value ("watchdog error: " + $_) -Encoding UTF8
    }
    Start-Sleep -Seconds $interval
}
