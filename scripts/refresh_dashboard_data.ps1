[CmdletBinding()]
param(
    [string]$AsOfDate,
    [switch]$Direct
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Не найден Python проекта: $Python"
}

$Arguments = @('-m', 'scripts.run_etl_with_vpn')
if ($AsOfDate) {
    $Arguments += @('--as-of-date', $AsOfDate)
}
if ($Direct) {
    $Arguments += '--direct'
}

Push-Location $ProjectRoot
try {
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "ETL завершился с кодом ошибки $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

