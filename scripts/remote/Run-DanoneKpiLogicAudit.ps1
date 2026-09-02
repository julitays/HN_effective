[CmdletBinding()]
param(
    [ValidatePattern('^\d{6}$')]
    [string]$YearMonth = '202607'
)

$ErrorActionPreference = 'Stop'
$auditScript = Join-Path $PSScriptRoot 'Audit-DanoneKpiLogic.ps1'

try {
    if (-not (Test-Path -LiteralPath $auditScript)) {
        throw "Не найден файл аудита: $auditScript"
    }

    Write-Host "Расширенный аудит KPI за $YearMonth"
    Write-Host 'Проверка может занять 10–20 минут. Прогресс будет показан по каждому блоку.'
    Write-Host ''

    & $auditScript `
        -YearMonth $YearMonth `
        -OutputRoot $PSScriptRoot `
        -TrustServerCertificate
}
catch {
    Write-Host ''
    Write-Error $_.Exception.Message
    exit 1
}
finally {
    Write-Host ''
    Read-Host 'Нажмите Enter, чтобы закрыть окно'
}
