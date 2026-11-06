# Обновление внешних рядов для shadow.py: Yahoo daily + FRED через curl.exe.
# Запускается из start_all.ps1 не чаще раза в 7 дней (проверка по штампу).
# Пишет только в data/external/, пути фиксированные.
$ErrorActionPreference = "Continue"
$DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$EXT = Join-Path $DIR "data\external"
$YAHOO = Join-Path $EXT "yahoo"
$FRED = Join-Path $EXT "fred"
$LOG = Join-Path $DIR "logs\shadow_refresh.log"
$stamp = Join-Path $EXT ".last_refresh"

if (-not (Test-Path $YAHOO)) { New-Item -ItemType Directory -Path $YAHOO -Force | Out-Null }
if (-not (Test-Path $FRED)) { New-Item -ItemType Directory -Path $FRED -Force | Out-Null }
if (-not (Test-Path (Join-Path $DIR "logs"))) { New-Item -ItemType Directory -Path (Join-Path $DIR "logs") -Force | Out-Null }

# Свежие данные — выходим, чтобы не дёргать источники каждые 5 минут.
if (Test-Path $stamp) {
    $age = (Get-Date) - (Get-Item $stamp).LastWriteTime
    if ($age.TotalDays -lt 7) { exit 0 }
}
$failed = 0

$UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
$end = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$symbols = @{
    "SPX" = "%5EGSPC"; "NDX" = "%5ENDX"; "DAX" = "%5EGDAXI"; "N225" = "%5EN225"
    "EURUSD" = "EURUSD%3DX"; "GBPUSD" = "GBPUSD%3DX"; "USDJPY" = "USDJPY%3DX"
    "AUDUSD" = "AUDUSD%3DX"; "USDCAD" = "USDCAD%3DX"; "USDCHF" = "USDCHF%3DX"
    "NZDUSD" = "NZDUSD%3DX"
}
foreach ($key in $symbols.Keys) {
    $dest = Join-Path $YAHOO "$key.json"
    $tmp = "$dest.tmp"
    $url = "https://query2.finance.yahoo.com/v8/finance/chart/$($symbols[$key])?period1=946684800&period2=$end&interval=1d"
    & curl.exe -s -f -m 45 -A $UA -o $tmp $url
    $head = if (Test-Path $tmp) { (Get-Content $tmp -TotalCount 1 -ErrorAction SilentlyContinue) } else { "" }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $tmp) -or (Get-Item $tmp).Length -lt 1000 -or $head -match '^\s*<') {
        Add-Content -Path $LOG -Value "$(Get-Date -Format s) $key yahoo: curl=$LASTEXITCODE"
        Remove-Item -Path $tmp -Force -ErrorAction SilentlyContinue
        $failed++
    } else {
        Move-Item -Path $tmp -Destination $dest -Force
    }
}
foreach ($id in @("VIXCLS", "DFII10", "DGS2", "DTWEXBGS")) {
    $dest = Join-Path $FRED "$id.csv"
    $tmp = "$dest.tmp"
    $url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=$id"
    & curl.exe -s -f -m 45 -o $tmp $url
    $head = if (Test-Path $tmp) { (Get-Content $tmp -TotalCount 1 -ErrorAction SilentlyContinue) } else { "" }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $tmp) -or (Get-Item $tmp).Length -lt 500 -or $head -match '^\s*<') {
        Add-Content -Path $LOG -Value "$(Get-Date -Format s) $id fred: curl=$LASTEXITCODE"
        Remove-Item -Path $tmp -Force -ErrorAction SilentlyContinue
        $failed++
    } else {
        Move-Item -Path $tmp -Destination $dest -Force
    }
}
if ($failed -eq 0) {
    Set-Content -Path $stamp -Value (Get-Date -Format o) -Encoding UTF8
} else {
    Remove-Item -Path $stamp -Force -ErrorAction SilentlyContinue
}
Add-Content -Path $LOG -Value "$(Get-Date -Format s) refreshed, errors=$failed"
exit 0
