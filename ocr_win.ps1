# OCR страниц через встроенный Windows.Media.Ocr.
# Использование: powershell -File ocr_win.ps1 -Dir "<папка с pNNN.png>" [-Lang ru|en-US]

param(
    [Parameter(Mandatory = $true)][string]$Dir,
    [string]$Lang = "ru"
)

Add-Type -AssemblyName System.Runtime.WindowsRuntime

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await($op, $type) {
    $task = $asTaskGeneric.MakeGenericMethod($type).Invoke($null, @($op))
    $task.Wait(-1) | Out-Null
    $task.Result
}

[Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime] | Out-Null

$language = New-Object Windows.Globalization.Language($Lang)
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
if ($null -eq $engine) {
    Write-Error "OCR-движок для языка $Lang недоступен"
    exit 1
}

$files = Get-ChildItem -Path $Dir -Filter "p*.png" | Sort-Object Name
$combined = Join-Path $Dir "combined_$Lang.txt"
"" | Set-Content -Path $combined -Encoding UTF8

foreach ($f in $files) {
    $txtPath = Join-Path $Dir ($f.BaseName + ".$Lang.txt")
    if (Test-Path $txtPath) { continue }

    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($f.FullName)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

    $text = ($result.Lines | ForEach-Object { $_.Text }) -join "`n"
    $text | Set-Content -Path $txtPath -Encoding UTF8
    Add-Content -Path $combined -Value ("`n===== " + $f.BaseName + " =====`n" + $text) -Encoding UTF8
    $stream.Dispose()
}
Write-Output "готово: $($files.Count) страниц, язык $Lang"
