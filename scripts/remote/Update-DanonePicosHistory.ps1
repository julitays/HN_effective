[CmdletBinding()]
param(
    [string]$Server = 'danoneDB.dedicorp.ru,1433',
    [string]$Database = 'HnnDW',
    [ValidatePattern('^\d{6}$')]
    [string]$FromYearMonth = '202601',
    [ValidatePattern('^\d{6}$')]
    [string]$ToYearMonth = '202607',
    [string]$OutputRoot = (Join-Path $env:LOCALAPPDATA 'HN\DanoneSQL\Exports'),
    [string]$TransferRoot = '\\tsclient\H',
    [string]$CredentialTarget = 'HN_Danone_SQL',
    [switch]$TrustServerCertificate
)

$ErrorActionPreference = 'Stop'

function ConvertFrom-YearMonth {
    param([Parameter(Mandatory)][string]$Value)
    [datetime]::ParseExact($Value, 'yyyyMM', [Globalization.CultureInfo]::InvariantCulture)
}

function New-ReadOnlyConnectionString {
    param(
        [Parameter(Mandatory)][string]$DataSource,
        [Parameter(Mandatory)][string]$InitialCatalog,
        [Parameter(Mandatory)][PSCredential]$Credential,
        [bool]$TrustCertificate
    )
    $builder = [Data.SqlClient.SqlConnectionStringBuilder]::new()
    $builder['Data Source'] = $DataSource
    $builder['Initial Catalog'] = $InitialCatalog
    $builder['User ID'] = $Credential.UserName
    $builder['Password'] = $Credential.GetNetworkCredential().Password
    $builder['Integrated Security'] = $false
    $builder['Application Name'] = 'HN PICOS History Update'
    $builder['Connect Timeout'] = 15
    $builder['Encrypt'] = $true
    $builder['TrustServerCertificate'] = $TrustCertificate
    $builder['ApplicationIntent'] = 'ReadOnly'
    $builder.ConnectionString
}

function Export-PicosQuery {
    param(
        [Parameter(Mandatory)][string]$ConnectionString,
        [Parameter(Mandatory)][string]$Query,
        [Parameter(Mandatory)][string]$Path
    )
    $connection = [Data.SqlClient.SqlConnection]::new($ConnectionString)
    $command = $connection.CreateCommand()
    $command.CommandText = $Query
    $command.CommandTimeout = 600
    $table = [Data.DataTable]::new()
    try {
        $connection.Open()
        $reader = $command.ExecuteReader()
        $table.Load($reader)
        if ($table.Rows.Count -eq 0) {
            $headers = @($table.Columns | ForEach-Object { $_.ColumnName })
            $line = ($headers | ForEach-Object { '"' + $_.Replace('"', '""') + '"' }) -join ','
            [IO.File]::WriteAllText($Path, $line + [Environment]::NewLine, [Text.UTF8Encoding]::new($true))
        }
        else {
            $table | Export-Csv -LiteralPath $Path -NoTypeInformation -Encoding UTF8
        }
        $table.Rows.Count
    }
    finally {
        $command.Dispose()
        $connection.Dispose()
    }
}

$startMonth = ConvertFrom-YearMonth $FromYearMonth
$endMonth = ConvertFrom-YearMonth $ToYearMonth
if ($startMonth -gt $endMonth) {
    throw "Начальный месяц позже конечного."
}

$credentialModule = Join-Path $PSScriptRoot 'DanoneSqlCredential.psm1'
Import-Module $credentialModule -Force
$credential = Get-HnWindowsCredential -Target $CredentialTarget
if (-not $credential) {
    throw "Учётные данные $CredentialTarget не найдены."
}
$connectionString = New-ReadOnlyConnectionString `
    -DataSource $Server `
    -InitialCatalog $Database `
    -Credential $credential `
    -TrustCertificate $TrustServerCertificate.IsPresent

$resolvedRoot = [IO.Path]::GetFullPath($OutputRoot).TrimEnd('\') + '\'
$month = $startMonth
while ($month -le $endMonth) {
    $yearMonth = $month.ToString('yyyyMM')
    $archivePath = Join-Path $OutputRoot "HN_KPI_$yearMonth.zip"
    $transferArchivePath = Join-Path $TransferRoot "HN_KPI_$yearMonth.zip"
    if (Test-Path -LiteralPath $transferArchivePath) {
        New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
        Copy-Item -LiteralPath $transferArchivePath -Destination $archivePath -Force
        Write-Host "PICOS ${yearMonth}: исходный пакет обновлён с обменного диска."
    }
    elseif (-not (Test-Path -LiteralPath $archivePath)) {
        throw "Не найден исходный пакет ни локально, ни на обменном диске: $archivePath"
    }
    $periodStartSql = $month.ToString('yyyy-MM-dd')
    $periodEndSql = $month.AddMonths(1).ToString('yyyy-MM-dd')
    $staging = Join-Path $OutputRoot ".picos_${yearMonth}_$([guid]::NewGuid().ToString('N'))"
    $resolvedStaging = [IO.Path]::GetFullPath($staging)
    if (-not $resolvedStaging.StartsWith($resolvedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Небезопасная временная папка: $resolvedStaging"
    }
    New-Item -ItemType Directory -Path $staging -Force | Out-Null
    try {
        Write-Host "PICOS ${yearMonth}: распаковка пакета..."
        Expand-Archive -LiteralPath $archivePath -DestinationPath $staging -Force
        $query = @"
WITH valid_visits AS (
    SELECT DISTINCT VISIT_ID
    FROM dbo.Fact_Visits
    WHERE VISIT_DATE >= '$periodStartSql'
      AND VISIT_DATE < '$periodEndSql'
      AND VISIT_IS_COMPLETE = 1
      AND PLACE_IS_CONFIRMED = 1
      AND ISNULL(isInvalid, 0) = 0
),
picos_visit AS (
    SELECT
        picos.VISIT_ID,
        picos.SHIP_TO,
        CAST(picos.VISIT_DATE AS date) AS VISIT_DATE,
        SUM(picos.PICOS_POTENTIAL_SCORE) AS PICOS_POTENTIAL,
        SUM(picos.PICOS_TARGET_SCORE) AS PICOS_PLAN,
        SUM(picos.PICOS_SCORE_FACT) AS PICOS_FACT
    FROM dbo.FACT_PICOS picos
    JOIN valid_visits valid_visit ON valid_visit.VISIT_ID = picos.VISIT_ID
    WHERE picos.VISIT_DATE >= '$periodStartSql'
      AND picos.VISIT_DATE < '$periodEndSql'
    GROUP BY picos.VISIT_ID, picos.SHIP_TO, CAST(picos.VISIT_DATE AS date)
    HAVING SUM(picos.PICOS_POTENTIAL_SCORE) > 0
       AND SUM(picos.PICOS_TARGET_SCORE) > 0
)
SELECT
    VISIT_ID AS visit_id,
    SHIP_TO AS store_id,
    VISIT_DATE AS visit_date,
    PICOS_POTENTIAL AS picos_potential,
    PICOS_PLAN AS picos_plan,
    PICOS_FACT AS picos_fact,
    CASE
        WHEN PICOS_FACT / NULLIF(PICOS_PLAN, 0.0) < 0.75 THEN 0.0
        WHEN PICOS_FACT / NULLIF(PICOS_PLAN, 0.0) > 1.0 THEN 1.0
        ELSE PICOS_FACT / NULLIF(PICOS_PLAN, 0.0)
    END AS picos_execution
FROM picos_visit;
"@
        $started = Get-Date
        Write-Host "PICOS ${yearMonth}: запрос..."
        $rows = Export-PicosQuery -ConnectionString $connectionString -Query $query -Path (Join-Path $staging 'picos_by_visit.csv')
        $seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 2)

        $manifestPath = Join-Path $staging 'manifest.csv'
        $manifest = @(Import-Csv -LiteralPath $manifestPath)
        $picosRow = $manifest | Where-Object { $_.file -eq 'picos_by_visit.csv' } | Select-Object -First 1
        if (-not $picosRow) {
            throw "В manifest.csv нет picos_by_visit.csv"
        }
        $picosRow.rows = [string]$rows
        if ($picosRow.PSObject.Properties.Name -contains 'seconds') {
            $picosRow.seconds = [string]$seconds
        }
        $manifest | Export-Csv -LiteralPath $manifestPath -NoTypeInformation -Encoding UTF8

        $temporaryArchive = Join-Path $OutputRoot ".HN_KPI_${yearMonth}_$([guid]::NewGuid().ToString('N')).zip"
        Compress-Archive -Path (Join-Path $staging '*') -DestinationPath $temporaryArchive -Force
        Move-Item -LiteralPath $temporaryArchive -Destination $archivePath -Force
        Copy-Item -LiteralPath $archivePath -Destination (Join-Path $TransferRoot (Split-Path $archivePath -Leaf)) -Force
        Write-Host "PICOS ${yearMonth}: $rows строк, пакет обновлён."
    }
    finally {
        if (Test-Path -LiteralPath $staging) {
            Remove-Item -LiteralPath $staging -Recurse -Force
        }
    }
    $month = $month.AddMonths(1)
}

Write-Host "PICOS обновлён: $FromYearMonth-$ToYearMonth"
