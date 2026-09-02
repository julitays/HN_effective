[CmdletBinding()]
param(
    [string]$Server = 'danoneDB.dedicorp.ru,1433',
    [string]$Database = 'HnnDW',
    [ValidatePattern('^\d{6}$')]
    [string]$YearMonth = (Get-Date -Format 'yyyyMM'),
    [string]$OutputRoot = (Join-Path $env:LOCALAPPDATA 'HN\DanoneSQL\Exports'),
    [string]$TransferRoot,
    [string]$CredentialTarget = 'HN_Danone_SQL',
    [switch]$TrustServerCertificate
)

$ErrorActionPreference = 'Stop'

function New-ReadOnlyConnectionString {
    param(
        [Parameter(Mandatory)]
        [string]$DataSource,
        [Parameter(Mandatory)]
        [string]$InitialCatalog,
        [Parameter(Mandatory)]
        [PSCredential]$Credential,
        [bool]$TrustCertificate
    )

    $builder = [System.Data.SqlClient.SqlConnectionStringBuilder]::new()
    $builder['Data Source'] = $DataSource
    $builder['Initial Catalog'] = $InitialCatalog
    $builder['User ID'] = $Credential.UserName
    $builder['Password'] = $Credential.GetNetworkCredential().Password
    $builder['Integrated Security'] = $false
    $builder['Application Name'] = 'HN KPI Prototype Export'
    $builder['Connect Timeout'] = 15
    $builder['Encrypt'] = $true
    $builder['TrustServerCertificate'] = $TrustCertificate
    $builder['ApplicationIntent'] = 'ReadOnly'
    return $builder.ConnectionString
}

function Export-ReadOnlyQuery {
    param(
        [Parameter(Mandatory)]
        [string]$ConnectionString,
        [Parameter(Mandatory)]
        [string]$Query,
        [Parameter(Mandatory)]
        [string]$Path,
        [int]$CommandTimeout = 600
    )

    $connection = [System.Data.SqlClient.SqlConnection]::new($ConnectionString)
    $command = $connection.CreateCommand()
    $command.CommandText = $Query
    $command.CommandTimeout = $CommandTimeout
    $table = [System.Data.DataTable]::new()
    try {
        $connection.Open()
        $reader = $command.ExecuteReader()
        $table.Load($reader)
        if ($table.Rows.Count -eq 0) {
            $headers = @($table.Columns | ForEach-Object { $_.ColumnName })
            New-EmptyCsv -Path $Path -Headers $headers
        }
        else {
            $table | Export-Csv -LiteralPath $Path -NoTypeInformation -Encoding UTF8
        }
        return $table.Rows.Count
    }
    finally {
        $command.Dispose()
        $connection.Dispose()
    }
}

function Test-ReadOnlyObject {
    param(
        [Parameter(Mandatory)]
        [string]$ConnectionString,
        [Parameter(Mandatory)]
        [string]$ObjectName
    )

    $connection = [System.Data.SqlClient.SqlConnection]::new($ConnectionString)
    $command = $connection.CreateCommand()
    $command.CommandText = 'SELECT CASE WHEN OBJECT_ID(@object_name) IS NULL THEN 0 ELSE 1 END;'
    [void]$command.Parameters.Add('@object_name', [System.Data.SqlDbType]::NVarChar, 256)
    $command.Parameters['@object_name'].Value = $ObjectName
    try {
        $connection.Open()
        return [bool][int]$command.ExecuteScalar()
    }
    finally {
        $command.Dispose()
        $connection.Dispose()
    }
}

function New-EmptyCsv {
    param(
        [Parameter(Mandatory)]
        [string]$Path,
        [Parameter(Mandatory)]
        [string[]]$Headers
    )

    $headerLine = ($Headers | ForEach-Object { '"' + $_.Replace('"', '""') + '"' }) -join ','
    [System.IO.File]::WriteAllText(
        $Path,
        $headerLine + [Environment]::NewLine,
        [System.Text.UTF8Encoding]::new($true)
    )
}

$year = [int]$YearMonth.Substring(0, 4)
$month = [int]$YearMonth.Substring(4, 2)
$periodStart = Get-Date -Year $year -Month $month -Day 1
$periodEnd = $periodStart.AddMonths(1)
$periodStartSql = $periodStart.ToString('yyyy-MM-dd')
$periodEndSql = $periodEnd.ToString('yyyy-MM-dd')
$osaTable = "dbo.Fact_OSA_$YearMonth"
$facingTable = "dbo.Fact_Facing_$YearMonth"

$credentialModule = Join-Path $PSScriptRoot 'DanoneSqlCredential.psm1'
if (-not (Test-Path -LiteralPath $credentialModule)) {
    throw "Не найден модуль учётных данных: $credentialModule"
}
Import-Module $credentialModule -Force
$credential = Get-HnWindowsCredential -Target $CredentialTarget
if (-not $credential) {
    throw "Учётные данные $CredentialTarget не найдены. Запустите Manage-DanoneSqlCredential.ps1 -Mode Set."
}
$connectionString = New-ReadOnlyConnectionString `
    -DataSource $Server `
    -InitialCatalog $Database `
    -Credential $credential `
    -TrustCertificate $TrustServerCertificate.IsPresent

$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$outputDirectory = Join-Path $OutputRoot ".staging_${YearMonth}_$timestamp"
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

$queries = @(
    @{
        Name = 'visits.csv'
        Query = @"
SELECT DISTINCT
    v.VISIT_ID AS visit_id,
    v.AGG_VISIT_ID AS aggregate_visit_id,
    CAST(v.VISIT_DATE AS date) AS visit_date,
    v.SHIP_TO AS store_id,
    v.Masterfid AS agent_master_id
FROM dbo.Fact_Visits v
WHERE v.VISIT_DATE >= '$periodStartSql'
  AND v.VISIT_DATE < '$periodEndSql'
  AND v.VISIT_IS_COMPLETE = 1
  AND v.PLACE_IS_CONFIRMED = 1
  AND ISNULL(v.isInvalid, 0) = 0;
"@
    },
    @{
        Name = 'agents.csv'
        Query = @"
WITH valid_agents AS (
    SELECT DISTINCT Masterfid
    FROM dbo.Fact_Visits
    WHERE VISIT_DATE >= '$periodStartSql'
      AND VISIT_DATE < '$periodEndSql'
      AND VISIT_IS_COMPLETE = 1
      AND PLACE_IS_CONFIRMED = 1
      AND ISNULL(isInvalid, 0) = 0
)
SELECT
    agent.Masterfid AS agent_master_id,
    MAX(agent.Exid) AS agent_login,
    MAX(agent.Name) AS agent_name,
    MAX(agent.Activeflag) AS is_active
FROM dbo.DIM_Agents agent
JOIN valid_agents valid_agent ON valid_agent.Masterfid = agent.Masterfid
GROUP BY agent.Masterfid;
"@
    },
    @{
        Name = 'stores.csv'
        Query = @"
WITH valid_stores AS (
    SELECT DISTINCT SHIP_TO
    FROM dbo.Fact_Visits
    WHERE VISIT_DATE >= '$periodStartSql'
      AND VISIT_DATE < '$periodEndSql'
      AND VISIT_IS_COMPLETE = 1
      AND PLACE_IS_CONFIRMED = 1
      AND ISNULL(isInvalid, 0) = 0
)
SELECT
    store.SHIP_TO AS store_id,
    MAX(store.CLIENT) AS network_code,
    MAX(store.ClientName) AS network_name,
    MAX(store.CHANNEL) AS store_format,
    MAX(store.CITY) AS city,
    MAX(store.BUSINESS_UNIT) AS business_unit,
    MAX(store.SALES_GROUP) AS sales_group
FROM dbo.Dim_POSList store
JOIN valid_stores valid_store ON valid_store.SHIP_TO = store.SHIP_TO
WHERE NULLIF(LTRIM(RTRIM(store.SHIP_TO)), '') IS NOT NULL
GROUP BY store.SHIP_TO;
"@
    },
    @{
        Name = 'picos_by_visit.csv'
        Headers = @(
            'visit_id', 'store_id', 'visit_date', 'picos_potential',
            'picos_plan', 'picos_fact', 'picos_execution'
        )
        Query = @"
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
    },
    @{
        Name = 'osa_by_visit.csv'
        RequiredTable = $osaTable
        Headers = @(
            'visit_id', 'store_id', 'visit_date', 'must_products',
            'must_products_in_stock', 'osa_fact_must', 'all_matrix_products',
            'all_matrix_products_in_stock', 'osa_fact_all_matrix'
        )
        Query = @"
SELECT
    o.VISIT_ID AS visit_id,
    p.SHIP_TO AS store_id,
    CAST(o.VISIT_DATE AS date) AS visit_date,
    SUM(CASE WHEN o.IN_MATRIX = 1 AND UPPER(LTRIM(RTRIM(ISNULL(o.MUST, '')))) = 'MUST' THEN 1 ELSE 0 END) AS must_products,
    SUM(CASE WHEN o.IN_MATRIX = 1 AND o.IN_STOCK = 1 AND UPPER(LTRIM(RTRIM(ISNULL(o.MUST, '')))) = 'MUST' THEN 1 ELSE 0 END) AS must_products_in_stock,
    CAST(SUM(CASE WHEN o.IN_MATRIX = 1 AND o.IN_STOCK = 1 AND UPPER(LTRIM(RTRIM(ISNULL(o.MUST, '')))) = 'MUST' THEN 1 ELSE 0 END) AS float)
        / NULLIF(SUM(CASE WHEN o.IN_MATRIX = 1 AND UPPER(LTRIM(RTRIM(ISNULL(o.MUST, '')))) = 'MUST' THEN 1 ELSE 0 END), 0) AS osa_fact_must,
    SUM(CASE WHEN o.IN_MATRIX = 1 THEN 1 ELSE 0 END) AS all_matrix_products,
    SUM(CASE WHEN o.IN_MATRIX = 1 AND o.IN_STOCK = 1 THEN 1 ELSE 0 END) AS all_matrix_products_in_stock,
    CAST(SUM(CASE WHEN o.IN_MATRIX = 1 AND o.IN_STOCK = 1 THEN 1 ELSE 0 END) AS float)
        / NULLIF(SUM(CASE WHEN o.IN_MATRIX = 1 THEN 1 ELSE 0 END), 0) AS osa_fact_all_matrix
FROM $osaTable o
JOIN (
    SELECT DISTINCT VISIT_ID
    FROM dbo.Fact_Visits
    WHERE VISIT_DATE >= '$periodStartSql'
      AND VISIT_DATE < '$periodEndSql'
      AND VISIT_IS_COMPLETE = 1
      AND PLACE_IS_CONFIRMED = 1
      AND ISNULL(isInvalid, 0) = 0
) valid_visit ON valid_visit.VISIT_ID = o.VISIT_ID
LEFT JOIN (
    SELECT fid, MAX(SHIP_TO) AS SHIP_TO
    FROM dbo.Dim_POSList
    GROUP BY fid
) p ON p.fid = o.fid
WHERE o.VISIT_DATE >= '$periodStartSql'
  AND o.VISIT_DATE < '$periodEndSql'
GROUP BY o.VISIT_ID, p.SHIP_TO, CAST(o.VISIT_DATE AS date);
"@
    },
    @{
        Name = 'top16_by_visit.csv'
        RequiredTable = $facingTable
        Headers = @(
            'visit_id', 'store_id', 'visit_date', 'observed_top16_products',
            'top16_facings', 'all_facings', 'group_facings_by_scene',
            'top16_share_all_facings', 'top16_share_group_facings'
        )
        CommandTimeout = 900
        Query = @"
SET NOCOUNT ON;

SELECT DISTINCT
    VISIT_ID
INTO #valid_visits
FROM dbo.Fact_Visits
WHERE VISIT_DATE >= '$periodStartSql'
  AND VISIT_DATE < '$periodEndSql'
  AND VISIT_IS_COMPLETE = 1
  AND PLACE_IS_CONFIRMED = 1
  AND ISNULL(isInvalid, 0) = 0;

CREATE CLUSTERED INDEX IX_valid_visits_id ON #valid_visits (VISIT_ID);

SELECT
    fid,
    MAX(SHIP_TO) AS SHIP_TO
INTO #positions
FROM dbo.Dim_POSList
GROUP BY fid;

CREATE CLUSTERED INDEX IX_positions_fid ON #positions (fid);

SELECT
    iid,
    MAX(PRODUCT_NATURE_CD) AS PRODUCT_NATURE_CD
INTO #products
FROM dbo.Dim_Products
GROUP BY iid;

CREATE CLUSTERED INDEX IX_products_iid ON #products (iid);

SELECT DISTINCT
        store_list.SHIP_TO,
        item_list.PRODUCT_NATURE_CD
INTO #active_top
FROM dbo.V_FACT_Faces_MustTop store_list
JOIN dbo.V_FACT_Items_MustTop item_list
  ON item_list.MustTopID = store_list.MustTopID
WHERE UPPER(LTRIM(RTRIM(item_list.ListType))) = 'TOP'
  AND store_list.StartDate < '$periodEndSql'
  AND (store_list.EndDate IS NULL OR store_list.EndDate >= '$periodStartSql')
  AND item_list.StartDate < '$periodEndSql'
  AND (item_list.EndDate IS NULL OR item_list.EndDate >= '$periodStartSql');

CREATE CLUSTERED INDEX IX_active_top_store_product
    ON #active_top (SHIP_TO, PRODUCT_NATURE_CD);

SELECT
    f.VISIT_ID,
    p.SHIP_TO,
    CAST(f.VISIT_DATE AS date) AS VISIT_DATE,
    f.PHOTO_AUDIT_ID,
    f.PHOTO_AUDIT_COUNTER,
    f.SCENE_TYPE_ID,
    f.iid,
    ISNULL(f.FACING_FACT, 0.0) AS FACING_FACT,
    ISNULL(f.GROUP_FACT, 0.0) AS GROUP_FACT,
    CASE WHEN active_top.PRODUCT_NATURE_CD IS NOT NULL THEN 1 ELSE 0 END AS IS_TOP16
INTO #facing_rows
FROM $facingTable f
JOIN #valid_visits valid_visit ON valid_visit.VISIT_ID = f.VISIT_ID
LEFT JOIN #positions p ON p.fid = f.fid
LEFT JOIN #products product ON product.iid = f.iid
LEFT JOIN #active_top active_top
  ON active_top.SHIP_TO = p.SHIP_TO
 AND active_top.PRODUCT_NATURE_CD = product.PRODUCT_NATURE_CD
WHERE f.VISIT_DATE >= '$periodStartSql'
  AND f.VISIT_DATE < '$periodEndSql';

CREATE CLUSTERED INDEX IX_facing_rows_visit
    ON #facing_rows (VISIT_ID, SHIP_TO, VISIT_DATE);

WITH
scene_totals AS (
    SELECT
        VISIT_ID,
        SHIP_TO,
        VISIT_DATE,
        PHOTO_AUDIT_ID,
        PHOTO_AUDIT_COUNTER,
        SCENE_TYPE_ID,
        MAX(GROUP_FACT) AS GROUP_FACT_SCENE
    FROM #facing_rows
    GROUP BY
        VISIT_ID,
        SHIP_TO,
        VISIT_DATE,
        PHOTO_AUDIT_ID,
        PHOTO_AUDIT_COUNTER,
        SCENE_TYPE_ID
),
visit_facings AS (
    SELECT
        VISIT_ID,
        SHIP_TO,
        VISIT_DATE,
        SUM(FACING_FACT) AS all_facings,
        SUM(CASE WHEN IS_TOP16 = 1 THEN FACING_FACT ELSE 0.0 END) AS top16_facings,
        COUNT_BIG(DISTINCT CASE WHEN IS_TOP16 = 1 THEN iid END) AS observed_top16_products
    FROM #facing_rows
    GROUP BY VISIT_ID, SHIP_TO, VISIT_DATE
),
visit_groups AS (
    SELECT
        VISIT_ID,
        SHIP_TO,
        VISIT_DATE,
        SUM(GROUP_FACT_SCENE) AS group_facings_by_scene
    FROM scene_totals
    GROUP BY VISIT_ID, SHIP_TO, VISIT_DATE
)
SELECT
    f.VISIT_ID AS visit_id,
    f.SHIP_TO AS store_id,
    f.VISIT_DATE AS visit_date,
    f.observed_top16_products,
    f.top16_facings,
    f.all_facings,
    g.group_facings_by_scene,
    f.top16_facings / NULLIF(f.all_facings, 0.0) AS top16_share_all_facings,
    f.top16_facings / NULLIF(g.group_facings_by_scene, 0.0) AS top16_share_group_facings
FROM visit_facings f
LEFT JOIN visit_groups g
  ON g.VISIT_ID = f.VISIT_ID
 AND g.SHIP_TO = f.SHIP_TO
 AND g.VISIT_DATE = f.VISIT_DATE
WHERE f.observed_top16_products > 0;
"@
    }
)

$errors = [System.Collections.Generic.List[object]]::new()
$warnings = [System.Collections.Generic.List[object]]::new()
$manifest = [System.Collections.Generic.List[object]]::new()
foreach ($item in $queries) {
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        if ($item.ContainsKey('RequiredTable') -and -not (Test-ReadOnlyObject -ConnectionString $connectionString -ObjectName $item.RequiredTable)) {
            Write-Host "Нет источника $($item.RequiredTable): создаётся пустой файл без подстановки данных."
            New-EmptyCsv -Path (Join-Path $outputDirectory $item.Name) -Headers $item.Headers
            $warnings.Add([pscustomobject]@{
                file = $item.Name
                warning = "Источник $($item.RequiredTable) отсутствует; данные не подставлялись."
            })
            $manifest.Add([pscustomobject]@{
                file = $item.Name
                rows = 0
                seconds = [Math]::Round($stopwatch.Elapsed.TotalSeconds, 2)
                status = 'source_absent'
            })
            continue
        }
        $timeout = if ($item.ContainsKey('CommandTimeout')) { [int]$item.CommandTimeout } else { 600 }
        Write-Host "Выгрузка $($item.Name)..."
        $rowCount = Export-ReadOnlyQuery `
            -ConnectionString $connectionString `
            -Query $item.Query `
            -Path (Join-Path $outputDirectory $item.Name) `
            -CommandTimeout $timeout
        $manifest.Add([pscustomobject]@{
            file = $item.Name
            rows = $rowCount
            seconds = [Math]::Round($stopwatch.Elapsed.TotalSeconds, 2)
            status = 'ok'
        })
    }
    catch {
        $errors.Add([pscustomobject]@{
            file = $item.Name
            error = $_.Exception.Message
        })
        $manifest.Add([pscustomobject]@{
            file = $item.Name
            rows = $null
            seconds = [Math]::Round($stopwatch.Elapsed.TotalSeconds, 2)
            status = 'error'
        })
    }
    finally {
        $stopwatch.Stop()
    }
}

$manifest | Export-Csv -LiteralPath (Join-Path $outputDirectory 'manifest.csv') -NoTypeInformation -Encoding UTF8

if ($errors.Count -gt 0) {
    $errors | Export-Csv -LiteralPath (Join-Path $outputDirectory 'errors.csv') -NoTypeInformation -Encoding UTF8
}
else {
    '"file","error"' | Set-Content -LiteralPath (Join-Path $outputDirectory 'errors.csv') -Encoding UTF8
}
if ($warnings.Count -gt 0) {
    $warnings | Export-Csv -LiteralPath (Join-Path $outputDirectory 'warnings.csv') -NoTypeInformation -Encoding UTF8
}
else {
    '"file","warning"' | Set-Content -LiteralPath (Join-Path $outputDirectory 'warnings.csv') -Encoding UTF8
}
@(
    "KPI monthly export",
    "Server: $Server",
    "Database: $Database",
    "Period: $YearMonth",
    "Created: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    "Errors: $($errors.Count)",
    "Warnings: $($warnings.Count)",
    '',
    'Read-only export. Client SCALE is not used.',
    'The selected month replaces only the same monthly partition.'
) | Set-Content -LiteralPath (Join-Path $outputDirectory 'README.txt') -Encoding UTF8

$resolvedRoot = [System.IO.Path]::GetFullPath($OutputRoot).TrimEnd('\') + '\'
$partitionDirectory = Join-Path $OutputRoot $YearMonth
$resolvedPartition = [System.IO.Path]::GetFullPath($partitionDirectory)
if (-not $resolvedPartition.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Небезопасный путь месячного раздела: $resolvedPartition"
}

if ($errors.Count -gt 0) {
    $failedArchivePath = Join-Path $OutputRoot "HN_KPI_${YearMonth}_FAILED_$timestamp.zip"
    Compress-Archive -Path (Join-Path $outputDirectory '*') -DestinationPath $failedArchivePath -Force
    if (-not [string]::IsNullOrWhiteSpace($TransferRoot) -and (Test-Path -LiteralPath $TransferRoot)) {
        Copy-Item -LiteralPath $failedArchivePath -Destination (Join-Path $TransferRoot (Split-Path $failedArchivePath -Leaf)) -Force
    }
    throw "Выгрузка месяца $YearMonth завершилась с ошибками. Действующий месячный раздел не изменён."
}

if (Test-Path -LiteralPath $partitionDirectory) {
    Remove-Item -LiteralPath $partitionDirectory -Recurse -Force
}
Move-Item -LiteralPath $outputDirectory -Destination $partitionDirectory

$archivePath = Join-Path $OutputRoot "HN_KPI_$YearMonth.zip"
Compress-Archive -Path (Join-Path $partitionDirectory '*') -DestinationPath $archivePath -Force
Write-Host "Готово: $archivePath"
if (-not [string]::IsNullOrWhiteSpace($TransferRoot)) {
    if (-not (Test-Path -LiteralPath $TransferRoot)) {
        throw "Папка передачи недоступна: $TransferRoot"
    }
    $transferredArchive = Join-Path $TransferRoot (Split-Path $archivePath -Leaf)
    Copy-Item -LiteralPath $archivePath -Destination $transferredArchive -Force
    Write-Host "ZIP передан: $transferredArchive"
}
