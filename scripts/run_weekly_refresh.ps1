[CmdletBinding()]
param(
    [string]$TransferRoot = 'H:\',
    [string]$AsOfDate,
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$SqlSource = $TransferRoot
$SqlTarget = Join-Path $ProjectRoot 'data\raw\kpi\sql_exports'
$LogRoot = Join-Path $ProjectRoot 'reports\weekly_refresh'
$RunId = Get-Date -Format 'yyyyMMdd_HHmmss'
$LogPath = Join-Path $LogRoot "weekly_refresh_$RunId.log"
$env:PYTHONUTF8 = '1'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Text.UTF8Encoding]::new($false)

function Invoke-CheckedProcess {
    param(
        [Parameter(Mandatory)][string]$Executable,
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$Label
    )

    Write-Host "`n=== $Label ==="
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label завершён с кодом ошибки $LASTEXITCODE"
    }
}

function Sync-ClientSqlPackages {
    if (-not (Test-Path -LiteralPath $SqlSource)) {
        throw "Обменный диск недоступен: $SqlSource"
    }

    $packages = @(
        Get-ChildItem -LiteralPath $SqlSource -File -Filter 'HN_KPI_*.zip' |
            Where-Object { $_.Name -match '^HN_KPI_\d{6}\.zip$' } |
            Sort-Object Name
    )
    if ($packages.Count -eq 0) {
        throw "На обменном диске нет пакетов HN_KPI_YYYYMM.zip"
    }

    New-Item -ItemType Directory -Path $SqlTarget -Force | Out-Null
    $updated = 0
    foreach ($source in $packages) {
        $target = Join-Path $SqlTarget $source.Name
        if (Test-Path -LiteralPath $target) {
            $sourceHash = (Get-FileHash -LiteralPath $source.FullName -Algorithm SHA256).Hash
            $targetHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
            if ($sourceHash -eq $targetHash) {
                Write-Host "Без изменений: $($source.Name)"
                continue
            }
        }

        $temporary = Join-Path $SqlTarget ".$($source.Name).incoming_$RunId"
        try {
            Copy-Item -LiteralPath $source.FullName -Destination $temporary -Force
            $sourceHash = (Get-FileHash -LiteralPath $source.FullName -Algorithm SHA256).Hash
            $temporaryHash = (Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash
            if ($sourceHash -ne $temporaryHash) {
                throw "Контрольная сумма не совпала после копирования $($source.Name)"
            }
            Move-Item -LiteralPath $temporary -Destination $target -Force
            Write-Host "Обновлён пакет: $($source.Name)"
            $updated++
        }
        finally {
            Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
        }
    }

    $latest = $packages | Sort-Object Name -Descending | Select-Object -First 1
    Write-Host "Пакетов на H: $($packages.Count); обновлено: $updated; последний: $($latest.Name)"
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Не найден Python проекта: $Python"
}

New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
Start-Transcript -LiteralPath $LogPath -Force | Out-Null
try {
    Push-Location $ProjectRoot
    try {
        Write-Host "Еженедельное обновление H&N: $RunId"
        Sync-ClientSqlPackages

        if (-not $SkipTests.IsPresent) {
            Invoke-CheckedProcess `
                -Executable $Python `
                -Arguments @('-m', 'pytest', '-q') `
                -Label 'Тесты проекта'
        }

        $etlArguments = @('-m', 'scripts.run_etl_with_vpn')
        if ($AsOfDate) {
            $etlArguments += @('--as-of-date', $AsOfDate)
        }
        Invoke-CheckedProcess `
            -Executable $Python `
            -Arguments $etlArguments `
            -Label 'Полный транзакционный ETL'

        Invoke-CheckedProcess `
            -Executable $Python `
            -Arguments @('-X', 'utf8', '-m', 'scripts.tools.audit_vitrines') `
            -Label 'Контроль опубликованных витрин'

        $manifestPath = Join-Path $ProjectRoot 'data\out\etl_run_manifest.json'
        if (-not (Test-Path -LiteralPath $manifestPath)) {
            throw "После ETL не найден манифест: $manifestPath"
        }
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 |
            ConvertFrom-Json
        Write-Host "`nОбновление завершено успешно. Run ID: $($manifest.run_id)"
        Write-Host "Журнал: $LogPath"
    }
    finally {
        Pop-Location
    }
}
catch {
    Write-Error "Еженедельное обновление остановлено: $($_.Exception.Message)" -ErrorAction Continue
    exit 1
}
finally {
    Stop-Transcript | Out-Null
}
