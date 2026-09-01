[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^\d{6}$')]
    [string]$FromYearMonth,
    [ValidatePattern('^\d{6}$')]
    [string]$ToYearMonth = (Get-Date -Format 'yyyyMM'),
    [string]$TransferRoot = '\\tsclient\H',
    [switch]$TrustServerCertificate
)

$ErrorActionPreference = 'Stop'

function ConvertFrom-YearMonth {
    param(
        [Parameter(Mandatory)]
        [string]$Value
    )

    return [datetime]::ParseExact(
        $Value,
        'yyyyMM',
        [System.Globalization.CultureInfo]::InvariantCulture
    )
}

$startMonth = ConvertFrom-YearMonth -Value $FromYearMonth
$endMonth = ConvertFrom-YearMonth -Value $ToYearMonth
if ($startMonth -gt $endMonth) {
    throw "Начальный месяц $FromYearMonth позже конечного месяца $ToYearMonth."
}

$monthlyExporter = Join-Path $PSScriptRoot 'Export-DanoneKpiPrototype.ps1'
if (-not (Test-Path -LiteralPath $monthlyExporter)) {
    throw "Не найден месячный выгрузчик: $monthlyExporter"
}

$month = $startMonth
while ($month -le $endMonth) {
    $yearMonth = $month.ToString('yyyyMM')
    Write-Host "Месячный раздел $yearMonth"
    & $monthlyExporter `
        -YearMonth $yearMonth `
        -TransferRoot $TransferRoot `
        -TrustServerCertificate:$TrustServerCertificate.IsPresent
    $month = $month.AddMonths(1)
}

Write-Host "Историческая выгрузка завершена: $FromYearMonth-$ToYearMonth"
