[CmdletBinding()]
param(
    [ValidatePattern('^\d{6}$')]
    [string]$YearMonth = (Get-Date -Format 'yyyyMM'),
    [string]$TransferRoot = '\\tsclient\H',
    [switch]$TrustServerCertificate
)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Text.UTF8Encoding]::new($false)
$Exporter = Join-Path $PSScriptRoot 'Export-DanoneKpiHistory.ps1'
if (-not (Test-Path -LiteralPath $Exporter)) {
    throw "Не найден выгрузчик: $Exporter"
}
if (-not (Test-Path -LiteralPath $TransferRoot)) {
    throw "Обменный диск недоступен: $TransferRoot"
}

$LogRoot = Join-Path $TransferRoot 'HN_SQL_logs'
New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
$LogPath = Join-Path $LogRoot "client_sql_${YearMonth}_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"

Start-Transcript -LiteralPath $LogPath -Force | Out-Null
try {
    Write-Host "Клиентская SQL: выгрузка месяца $YearMonth"
    & $Exporter `
        -FromYearMonth $YearMonth `
        -ToYearMonth $YearMonth `
        -TransferRoot $TransferRoot `
        -TrustServerCertificate:$TrustServerCertificate.IsPresent
    $archive = Join-Path $TransferRoot "HN_KPI_$YearMonth.zip"
    if (-not (Test-Path -LiteralPath $archive)) {
        throw "Итоговый пакет не появился на обменном диске: $archive"
    }
    Write-Host "Выгрузка завершена: $archive"
    Write-Host "Журнал: $LogPath"
}
finally {
    Stop-Transcript | Out-Null
}
