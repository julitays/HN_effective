[CmdletBinding()]
param(
    [string]$Server = 'corphnDB.dedicorp.ru,1433',
    [string]$Database = 'HnnDW',
    [string]$CredentialTarget = 'HN_Danone_SQL',
    [ValidatePattern('^\d{6}$')]
    [string]$YearMonth = '202607',
    [string]$OutputRoot = $PSScriptRoot,
    [switch]$TrustServerCertificate
)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Text.UTF8Encoding]::new($false)

$credentialModule = Join-Path $PSScriptRoot 'DanoneSqlCredential.psm1'
if (-not (Test-Path -LiteralPath $credentialModule)) {
    throw "Не найден модуль Windows Credential Manager: $credentialModule"
}
Import-Module $credentialModule -Force

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
    $builder['Application Name'] = 'HN KPI Logic Read-Only Audit'
    $builder['Connect Timeout'] = 20
    $builder['Encrypt'] = $true
    $builder['TrustServerCertificate'] = $TrustCertificate
    $builder['ApplicationIntent'] = 'ReadOnly'
    return $builder.ConnectionString
}

function Invoke-ReadOnlyQuery {
    param(
        [Parameter(Mandatory)]
        [string]$ConnectionString,
        [Parameter(Mandatory)]
        [string]$Query,
        [int]$CommandTimeout = 180
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
        $reader.Dispose()
        return (, $table)
    }
    finally {
        $command.Dispose()
        $connection.Dispose()
    }
}

function Export-AuditQuery {
    param(
        [Parameter(Mandatory)]
        [string]$Name,
        [Parameter(Mandatory)]
        [string]$Query,
        [int]$CommandTimeout = 180
    )

    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    Write-Host "[$Name] выполняется..."
    try {
        $result = Invoke-ReadOnlyQuery `
            -ConnectionString $script:ConnectionString `
            -Query $Query `
            -CommandTimeout $CommandTimeout
        $path = Join-Path $script:OutputDirectory "$Name.csv"
        $result | Export-Csv -LiteralPath $path -NoTypeInformation -Encoding UTF8
        $script:Manifest.Add([pscustomobject]@{
            file = "$Name.csv"
            rows = $result.Rows.Count
            seconds = [Math]::Round($stopwatch.Elapsed.TotalSeconds, 2)
            status = 'ok'
            error = ''
        })
        Write-Host "[$Name] готово: $($result.Rows.Count) строк, $([Math]::Round($stopwatch.Elapsed.TotalSeconds, 1)) сек."
    }
    catch {
        $message = $_.Exception.Message
        $script:Manifest.Add([pscustomobject]@{
            file = "$Name.csv"
            rows = 0
            seconds = [Math]::Round($stopwatch.Elapsed.TotalSeconds, 2)
            status = 'error'
            error = $message
        })
        Write-Warning "[$Name] ошибка: $message"
    }
}

$credential = Get-HnWindowsCredential -Target $CredentialTarget
if (-not $credential) {
    throw "Учётные данные $CredentialTarget не найдены в Windows Credential Manager."
}

$monthStart = [datetime]::ParseExact("${YearMonth}01", 'yyyyMMdd', [Globalization.CultureInfo]::InvariantCulture)
$monthEnd = $monthStart.AddMonths(1)
$periodStartSql = $monthStart.ToString('yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
$periodEndSql = $monthEnd.ToString('yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
$OutputDirectory = Join-Path $OutputRoot "HN_KPI_Logic_Audit_${YearMonth}_$timestamp"
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null

$ConnectionString = New-ReadOnlyConnectionString `
    -DataSource $Server `
    -InitialCatalog $Database `
    -Credential $credential `
    -TrustCertificate $TrustServerCertificate.IsPresent

$Manifest = [Collections.Generic.List[object]]::new()

$serverInfoQuery = @"
SELECT
    CAST(SERVERPROPERTY('ServerName') AS nvarchar(256)) AS server_name,
    CAST(SERVERPROPERTY('MachineName') AS nvarchar(256)) AS machine_name,
    CAST(SERVERPROPERTY('InstanceName') AS nvarchar(256)) AS instance_name,
    CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)) AS product_version,
    DB_NAME() AS current_database,
    ORIGINAL_LOGIN() AS login_name,
    @@SERVERNAME AS server_alias,
    GETDATE() AS server_time;
"@
Export-AuditQuery -Name 'server_info' -Query $serverInfoQuery

$objectsQuery = @"
SELECT
    s.name AS schema_name,
    o.name AS object_name,
    o.type AS object_type_code,
    o.type_desc AS object_type,
    o.create_date,
    o.modify_date,
    CAST(HAS_PERMS_BY_NAME(QUOTENAME(s.name) + '.' + QUOTENAME(o.name), 'OBJECT', 'SELECT') AS int) AS can_select,
    CAST(HAS_PERMS_BY_NAME(QUOTENAME(s.name) + '.' + QUOTENAME(o.name), 'OBJECT', 'VIEW DEFINITION') AS int) AS can_view_definition
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
WHERE o.is_ms_shipped = 0
ORDER BY s.name, o.type_desc, o.name;
"@
Export-AuditQuery -Name 'all_objects' -Query $objectsQuery

$columnsQuery = @"
SELECT
    s.name AS schema_name,
    o.name AS object_name,
    o.type_desc AS object_type,
    c.column_id,
    c.name AS column_name,
    t.name AS data_type,
    c.max_length,
    c.precision,
    c.scale,
    c.is_nullable,
    c.is_computed
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
JOIN sys.columns c ON c.object_id = o.object_id
JOIN sys.types t ON t.user_type_id = c.user_type_id
WHERE o.is_ms_shipped = 0
ORDER BY s.name, o.name, c.column_id;
"@
Export-AuditQuery -Name 'all_columns' -Query $columnsQuery

$keywordObjectsQuery = @"
WITH object_text AS (
    SELECT
        s.name AS schema_name,
        o.name AS object_name,
        o.type_desc AS object_type,
        LOWER(o.name) AS search_text
    FROM sys.objects o
    JOIN sys.schemas s ON s.schema_id = o.schema_id
    WHERE o.is_ms_shipped = 0
),
column_text AS (
    SELECT
        s.name AS schema_name,
        o.name AS object_name,
        o.type_desc AS object_type,
        LOWER(STRING_AGG(CONVERT(nvarchar(max), c.name), N' | ')) AS search_text
    FROM sys.objects o
    JOIN sys.schemas s ON s.schema_id = o.schema_id
    JOIN sys.columns c ON c.object_id = o.object_id
    WHERE o.is_ms_shipped = 0
    GROUP BY s.name, o.name, o.type_desc
),
combined AS (
    SELECT schema_name, object_name, object_type, search_text FROM object_text
    UNION ALL
    SELECT schema_name, object_name, object_type, search_text FROM column_text
)
SELECT DISTINCT schema_name, object_name, object_type
FROM combined
WHERE search_text LIKE '%kpi%'
   OR search_text LIKE '%picos%'
   OR search_text LIKE '%osa%'
   OR search_text LIKE '%top16%'
   OR search_text LIKE '%top_16%'
   OR search_text LIKE '%top 16%'
   OR search_text LIKE '%musttop%'
   OR search_text LIKE '%scale%'
   OR search_text LIKE '%target%'
   OR search_text LIKE '%goal%'
   OR search_text LIKE '%plan%'
   OR search_text LIKE N'%цель%'
   OR search_text LIKE N'%план%'
ORDER BY schema_name, object_name;
"@
Export-AuditQuery -Name 'keyword_objects' -Query $keywordObjectsQuery

$keywordColumnsQuery = @"
SELECT
    s.name AS schema_name,
    o.name AS object_name,
    o.type_desc AS object_type,
    c.column_id,
    c.name AS column_name,
    t.name AS data_type
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
JOIN sys.columns c ON c.object_id = o.object_id
JOIN sys.types t ON t.user_type_id = c.user_type_id
WHERE o.is_ms_shipped = 0
  AND (
       LOWER(c.name) LIKE '%kpi%'
    OR LOWER(c.name) LIKE '%picos%'
    OR LOWER(c.name) LIKE '%osa%'
    OR LOWER(c.name) LIKE '%top%'
    OR LOWER(c.name) LIKE '%must%'
    OR LOWER(c.name) LIKE '%scale%'
    OR LOWER(c.name) LIKE '%target%'
    OR LOWER(c.name) LIKE '%goal%'
    OR LOWER(c.name) LIKE '%plan%'
    OR LOWER(c.name) LIKE '%fact%'
    OR LOWER(c.name) LIKE '%result%'
    OR LOWER(c.name) LIKE '%exec%'
    OR LOWER(c.name) LIKE N'%цель%'
    OR LOWER(c.name) LIKE N'%план%'
  )
ORDER BY s.name, o.name, c.column_id;
"@
Export-AuditQuery -Name 'keyword_columns' -Query $keywordColumnsQuery

$modulesQuery = @"
SELECT
    s.name AS schema_name,
    o.name AS object_name,
    o.type_desc AS object_type,
    CAST(HAS_PERMS_BY_NAME(QUOTENAME(s.name) + '.' + QUOTENAME(o.name), 'OBJECT', 'VIEW DEFINITION') AS int) AS can_view_definition,
    CASE WHEN m.definition IS NULL THEN 0 ELSE 1 END AS definition_visible,
    m.definition
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
LEFT JOIN sys.sql_modules m ON m.object_id = o.object_id
WHERE o.is_ms_shipped = 0
  AND o.type IN ('V', 'P', 'PC', 'FN', 'IF', 'TF', 'FS', 'FT', 'TR')
  AND (
       LOWER(o.name) LIKE '%kpi%'
    OR LOWER(o.name) LIKE '%picos%'
    OR LOWER(o.name) LIKE '%osa%'
    OR LOWER(o.name) LIKE '%top%'
    OR LOWER(o.name) LIKE '%must%'
    OR LOWER(o.name) LIKE '%scale%'
    OR LOWER(COALESCE(m.definition, '')) LIKE '%kpi%'
    OR LOWER(COALESCE(m.definition, '')) LIKE '%picos%'
    OR LOWER(COALESCE(m.definition, '')) LIKE '%osa%'
    OR LOWER(COALESCE(m.definition, '')) LIKE '%top16%'
    OR LOWER(COALESCE(m.definition, '')) LIKE '%top_16%'
    OR LOWER(COALESCE(m.definition, '')) LIKE '%top 16%'
    OR LOWER(COALESCE(m.definition, '')) LIKE '%musttop%'
    OR LOWER(COALESCE(m.definition, '')) LIKE '%scale%'
    OR LOWER(COALESCE(m.definition, '')) LIKE '%target%'
    OR LOWER(COALESCE(m.definition, '')) LIKE N'%цель%'
  )
ORDER BY s.name, o.name;
"@
Export-AuditQuery -Name 'keyword_sql_modules' -Query $modulesQuery

$synonymsQuery = @"
SELECT
    s.name AS schema_name,
    sy.name AS synonym_name,
    sy.base_object_name,
    sy.create_date,
    sy.modify_date
FROM sys.synonyms sy
JOIN sys.schemas s ON s.schema_id = sy.schema_id
ORDER BY s.name, sy.name;
"@
Export-AuditQuery -Name 'synonyms' -Query $synonymsQuery

$dependenciesQuery = @"
SELECT
    OBJECT_SCHEMA_NAME(d.referencing_id) AS referencing_schema,
    OBJECT_NAME(d.referencing_id) AS referencing_object,
    o.type_desc AS referencing_type,
    d.referenced_server_name,
    d.referenced_database_name,
    d.referenced_schema_name,
    d.referenced_entity_name,
    d.is_ambiguous
FROM sys.sql_expression_dependencies d
LEFT JOIN sys.objects o ON o.object_id = d.referencing_id
WHERE LOWER(COALESCE(OBJECT_NAME(d.referencing_id), '')) LIKE '%kpi%'
   OR LOWER(COALESCE(OBJECT_NAME(d.referencing_id), '')) LIKE '%picos%'
   OR LOWER(COALESCE(OBJECT_NAME(d.referencing_id), '')) LIKE '%osa%'
   OR LOWER(COALESCE(OBJECT_NAME(d.referencing_id), '')) LIKE '%top%'
   OR LOWER(COALESCE(d.referenced_entity_name, '')) LIKE '%kpi%'
   OR LOWER(COALESCE(d.referenced_entity_name, '')) LIKE '%picos%'
   OR LOWER(COALESCE(d.referenced_entity_name, '')) LIKE '%osa%'
   OR LOWER(COALESCE(d.referenced_entity_name, '')) LIKE '%top%'
ORDER BY referencing_schema, referencing_object, referenced_database_name, referenced_entity_name;
"@
Export-AuditQuery -Name 'keyword_dependencies' -Query $dependenciesQuery

$osaProfileQuery = @"
WITH positions AS (
    SELECT fid, MAX(SHIP_TO) AS SHIP_TO
    FROM dbo.Dim_POSList
    GROUP BY fid
)
SELECT
    ISNULL(CAST(o.PICOS AS int), -1) AS picos_flag,
    COALESCE(NULLIF(LTRIM(RTRIM(o.MUST)), ''), '(empty)') AS must_value,
    COUNT_BIG(*) AS source_rows,
    COUNT_BIG(DISTINCT o.VISIT_ID) AS visits,
    COUNT_BIG(DISTINCT p.SHIP_TO) AS stores,
    COUNT_BIG(DISTINCT o.iid) AS products,
    SUM(CASE WHEN o.IN_MATRIX = 1 THEN 1 ELSE 0 END) AS rows_in_matrix,
    SUM(CASE WHEN o.IN_MATRIX = 1 AND o.IN_STOCK = 1 THEN 1 ELSE 0 END) AS rows_stock_flag_1,
    SUM(CASE WHEN o.IN_MATRIX = 1 AND ISNULL(o.IN_STOCK, 0) = 0 THEN 1 ELSE 0 END) AS rows_stock_flag_0
FROM dbo.Fact_OSA o
LEFT JOIN positions p ON p.fid = o.fid
WHERE o.VISIT_DATE >= '$periodStartSql'
  AND o.VISIT_DATE < '$periodEndSql'
GROUP BY
    ISNULL(CAST(o.PICOS AS int), -1),
    COALESCE(NULLIF(LTRIM(RTRIM(o.MUST)), ''), '(empty)')
ORDER BY picos_flag, must_value;
"@
Export-AuditQuery -Name "profile_osa_flags_$YearMonth" -Query $osaProfileQuery -CommandTimeout 900

$osaCandidatesQuery = @"
WITH valid_visits AS (
    SELECT DISTINCT VISIT_ID
    FROM dbo.Fact_Visits
    WHERE VISIT_DATE >= '$periodStartSql'
      AND VISIT_DATE < '$periodEndSql'
      AND VISIT_IS_COMPLETE = 1
      AND PLACE_IS_CONFIRMED = 1
      AND ISNULL(isInvalid, 0) = 0
),
positions AS (
    SELECT fid, MAX(SHIP_TO) AS SHIP_TO
    FROM dbo.Dim_POSList
    GROUP BY fid
),
visit_values AS (
    SELECT
        o.VISIT_ID,
        p.SHIP_TO,
        CAST(o.VISIT_DATE AS date) AS VISIT_DATE,
        ISNULL(CAST(o.PICOS AS int), -1) AS picos_flag,
        SUM(CASE WHEN o.IN_MATRIX = 1 THEN 1 ELSE 0 END) AS all_matrix,
        SUM(CASE WHEN o.IN_MATRIX = 1 AND o.IN_STOCK = 1 THEN 1 ELSE 0 END) AS all_stock_flag_1,
        SUM(CASE WHEN o.IN_MATRIX = 1 AND ISNULL(o.IN_STOCK, 0) = 0 THEN 1 ELSE 0 END) AS all_stock_flag_0,
        SUM(CASE WHEN o.IN_MATRIX = 1 AND UPPER(LTRIM(RTRIM(ISNULL(o.MUST, '')))) = 'MUST' THEN 1 ELSE 0 END) AS must_matrix,
        SUM(CASE WHEN o.IN_MATRIX = 1 AND o.IN_STOCK = 1 AND UPPER(LTRIM(RTRIM(ISNULL(o.MUST, '')))) = 'MUST' THEN 1 ELSE 0 END) AS must_stock_flag_1,
        SUM(CASE WHEN o.IN_MATRIX = 1 AND ISNULL(o.IN_STOCK, 0) = 0 AND UPPER(LTRIM(RTRIM(ISNULL(o.MUST, '')))) = 'MUST' THEN 1 ELSE 0 END) AS must_stock_flag_0,
        SUM(CASE WHEN o.IN_MATRIX = 1 AND UPPER(LTRIM(RTRIM(ISNULL(o.MUST, '')))) = 'TOP' THEN 1 ELSE 0 END) AS top_matrix,
        SUM(CASE WHEN o.IN_MATRIX = 1 AND o.IN_STOCK = 1 AND UPPER(LTRIM(RTRIM(ISNULL(o.MUST, '')))) = 'TOP' THEN 1 ELSE 0 END) AS top_stock_flag_1,
        SUM(CASE WHEN o.IN_MATRIX = 1 AND ISNULL(o.IN_STOCK, 0) = 0 AND UPPER(LTRIM(RTRIM(ISNULL(o.MUST, '')))) = 'TOP' THEN 1 ELSE 0 END) AS top_stock_flag_0
    FROM dbo.Fact_OSA o
    JOIN valid_visits v ON v.VISIT_ID = o.VISIT_ID
    LEFT JOIN positions p ON p.fid = o.fid
    WHERE o.VISIT_DATE >= '$periodStartSql'
      AND o.VISIT_DATE < '$periodEndSql'
    GROUP BY o.VISIT_ID, p.SHIP_TO, CAST(o.VISIT_DATE AS date), ISNULL(CAST(o.PICOS AS int), -1)
)
SELECT
    SHIP_TO AS store_id,
    picos_flag,
    COUNT_BIG(*) AS visits,
    AVG(CAST(all_stock_flag_1 AS float) / NULLIF(all_matrix, 0)) AS avg_all_flag_1,
    AVG(CAST(all_stock_flag_0 AS float) / NULLIF(all_matrix, 0)) AS avg_all_flag_0,
    CAST(SUM(all_stock_flag_1) AS float) / NULLIF(SUM(all_matrix), 0) AS weighted_all_flag_1,
    CAST(SUM(all_stock_flag_0) AS float) / NULLIF(SUM(all_matrix), 0) AS weighted_all_flag_0,
    AVG(CAST(must_stock_flag_1 AS float) / NULLIF(must_matrix, 0)) AS avg_must_flag_1,
    AVG(CAST(must_stock_flag_0 AS float) / NULLIF(must_matrix, 0)) AS avg_must_flag_0,
    CAST(SUM(must_stock_flag_1) AS float) / NULLIF(SUM(must_matrix), 0) AS weighted_must_flag_1,
    CAST(SUM(must_stock_flag_0) AS float) / NULLIF(SUM(must_matrix), 0) AS weighted_must_flag_0,
    AVG(CAST(top_stock_flag_1 AS float) / NULLIF(top_matrix, 0)) AS avg_top_flag_1,
    AVG(CAST(top_stock_flag_0 AS float) / NULLIF(top_matrix, 0)) AS avg_top_flag_0,
    CAST(SUM(top_stock_flag_1) AS float) / NULLIF(SUM(top_matrix), 0) AS weighted_top_flag_1,
    CAST(SUM(top_stock_flag_0) AS float) / NULLIF(SUM(top_matrix), 0) AS weighted_top_flag_0
FROM visit_values
WHERE SHIP_TO IS NOT NULL
GROUP BY SHIP_TO, picos_flag
ORDER BY SHIP_TO, picos_flag;
"@
Export-AuditQuery -Name "profile_osa_candidates_$YearMonth" -Query $osaCandidatesQuery -CommandTimeout 1200

$facingProfileQuery = @"
WITH positions AS (
    SELECT fid, MAX(SHIP_TO) AS SHIP_TO
    FROM dbo.Dim_POSList
    GROUP BY fid
)
SELECT
    ISNULL(CAST(f.PICOS AS int), -1) AS picos_flag,
    COUNT_BIG(*) AS source_rows,
    COUNT_BIG(DISTINCT f.VISIT_ID) AS visits,
    COUNT_BIG(DISTINCT p.SHIP_TO) AS stores,
    COUNT_BIG(DISTINCT f.iid) AS products,
    SUM(ISNULL(f.FACING_FACT, 0.0)) AS facing_fact,
    SUM(ISNULL(f.GROUP_FACT, 0.0)) AS group_fact
FROM dbo.Fact_Facing f
LEFT JOIN positions p ON p.fid = f.fid
WHERE f.VISIT_DATE >= '$periodStartSql'
  AND f.VISIT_DATE < '$periodEndSql'
GROUP BY ISNULL(CAST(f.PICOS AS int), -1)
ORDER BY picos_flag;
"@
Export-AuditQuery -Name "profile_facing_flags_$YearMonth" -Query $facingProfileQuery -CommandTimeout 900

$topCandidatesQuery = @"
SET NOCOUNT ON;

SELECT DISTINCT VISIT_ID
INTO #valid_visits
FROM dbo.Fact_Visits
WHERE VISIT_DATE >= '$periodStartSql'
  AND VISIT_DATE < '$periodEndSql'
  AND VISIT_IS_COMPLETE = 1
  AND PLACE_IS_CONFIRMED = 1
  AND ISNULL(isInvalid, 0) = 0;

CREATE CLUSTERED INDEX IX_valid_visits_id ON #valid_visits (VISIT_ID);

SELECT fid, MAX(SHIP_TO) AS SHIP_TO
INTO #positions
FROM dbo.Dim_POSList
GROUP BY fid;

CREATE CLUSTERED INDEX IX_positions_fid ON #positions (fid);

SELECT iid, MAX(PRODUCT_NATURE_CD) AS PRODUCT_NATURE_CD
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

CREATE CLUSTERED INDEX IX_active_top_store_product ON #active_top (SHIP_TO, PRODUCT_NATURE_CD);

SELECT
    f.VISIT_ID,
    p.SHIP_TO,
    CAST(f.VISIT_DATE AS date) AS VISIT_DATE,
    ISNULL(CAST(f.PICOS AS int), -1) AS picos_flag,
    f.PHOTO_AUDIT_ID,
    f.PHOTO_AUDIT_COUNTER,
    f.SCENE_TYPE_ID,
    f.iid,
    ISNULL(f.FACING_FACT, 0.0) AS FACING_FACT,
    ISNULL(f.GROUP_FACT, 0.0) AS GROUP_FACT,
    CASE WHEN active_top.PRODUCT_NATURE_CD IS NOT NULL THEN 1 ELSE 0 END AS is_top16
INTO #facing_rows
FROM dbo.Fact_Facing f
JOIN #valid_visits valid_visit ON valid_visit.VISIT_ID = f.VISIT_ID
LEFT JOIN #positions p ON p.fid = f.fid
LEFT JOIN #products product ON product.iid = f.iid
LEFT JOIN #active_top active_top
  ON active_top.SHIP_TO = p.SHIP_TO
 AND active_top.PRODUCT_NATURE_CD = product.PRODUCT_NATURE_CD
WHERE f.VISIT_DATE >= '$periodStartSql'
  AND f.VISIT_DATE < '$periodEndSql';

CREATE CLUSTERED INDEX IX_facing_rows_visit ON #facing_rows (VISIT_ID, SHIP_TO, VISIT_DATE, picos_flag);

WITH scene_totals AS (
    SELECT
        VISIT_ID,
        SHIP_TO,
        VISIT_DATE,
        picos_flag,
        PHOTO_AUDIT_ID,
        PHOTO_AUDIT_COUNTER,
        SCENE_TYPE_ID,
        MAX(GROUP_FACT) AS group_fact_scene
    FROM #facing_rows
    GROUP BY VISIT_ID, SHIP_TO, VISIT_DATE, picos_flag, PHOTO_AUDIT_ID, PHOTO_AUDIT_COUNTER, SCENE_TYPE_ID
),
visit_facings AS (
    SELECT
        VISIT_ID,
        SHIP_TO,
        VISIT_DATE,
        picos_flag,
        SUM(FACING_FACT) AS all_facings,
        SUM(CASE WHEN is_top16 = 1 THEN FACING_FACT ELSE 0.0 END) AS top16_facings,
        COUNT_BIG(DISTINCT CASE WHEN is_top16 = 1 THEN iid END) AS observed_top16_products
    FROM #facing_rows
    GROUP BY VISIT_ID, SHIP_TO, VISIT_DATE, picos_flag
),
visit_groups AS (
    SELECT
        VISIT_ID,
        SHIP_TO,
        VISIT_DATE,
        picos_flag,
        SUM(group_fact_scene) AS group_facings_by_scene
    FROM scene_totals
    GROUP BY VISIT_ID, SHIP_TO, VISIT_DATE, picos_flag
),
visit_values AS (
    SELECT
        f.VISIT_ID,
        f.SHIP_TO,
        f.VISIT_DATE,
        f.picos_flag,
        f.observed_top16_products,
        f.top16_facings,
        f.all_facings,
        g.group_facings_by_scene,
        f.top16_facings / NULLIF(f.all_facings, 0.0) AS share_all_facings,
        f.top16_facings / NULLIF(g.group_facings_by_scene, 0.0) AS share_group_facings
    FROM visit_facings f
    LEFT JOIN visit_groups g
      ON g.VISIT_ID = f.VISIT_ID
     AND g.SHIP_TO = f.SHIP_TO
     AND g.VISIT_DATE = f.VISIT_DATE
     AND g.picos_flag = f.picos_flag
    WHERE f.observed_top16_products > 0
)
SELECT
    SHIP_TO AS store_id,
    picos_flag,
    COUNT_BIG(*) AS visits,
    AVG(share_all_facings) AS avg_share_all_facings,
    AVG(share_group_facings) AS avg_share_group_facings,
    SUM(top16_facings) / NULLIF(SUM(all_facings), 0.0) AS weighted_share_all_facings,
    SUM(top16_facings) / NULLIF(SUM(group_facings_by_scene), 0.0) AS weighted_share_group_facings,
    AVG(CAST(observed_top16_products AS float)) AS avg_observed_top16_products
FROM visit_values
WHERE SHIP_TO IS NOT NULL
GROUP BY SHIP_TO, picos_flag
ORDER BY SHIP_TO, picos_flag;
"@
Export-AuditQuery -Name "profile_top16_candidates_$YearMonth" -Query $topCandidatesQuery -CommandTimeout 1800

$mustTopQuery = @"
SELECT
    UPPER(LTRIM(RTRIM(item_list.ListType))) AS list_type,
    store.CLIENT AS network_code,
    MAX(store.ClientName) AS network_name,
    COUNT_BIG(DISTINCT store_list.SHIP_TO) AS stores,
    COUNT_BIG(DISTINCT store_list.MustTopID) AS list_ids,
    COUNT_BIG(DISTINCT item_list.PRODUCT_NATURE_CD) AS products,
    MIN(store_list.StartDate) AS min_store_start_date,
    MAX(store_list.EndDate) AS max_store_end_date
FROM dbo.V_FACT_Faces_MustTop store_list
JOIN dbo.V_FACT_Items_MustTop item_list ON item_list.MustTopID = store_list.MustTopID
LEFT JOIN dbo.Dim_POSList store ON store.SHIP_TO = store_list.SHIP_TO
WHERE store_list.StartDate < '$periodEndSql'
  AND (store_list.EndDate IS NULL OR store_list.EndDate >= '$periodStartSql')
  AND item_list.StartDate < '$periodEndSql'
  AND (item_list.EndDate IS NULL OR item_list.EndDate >= '$periodStartSql')
GROUP BY UPPER(LTRIM(RTRIM(item_list.ListType))), store.CLIENT
ORDER BY list_type, network_code;
"@
Export-AuditQuery -Name "profile_musttop_assignment_$YearMonth" -Query $mustTopQuery -CommandTimeout 600

$routeRolesQuery = @"
SELECT
    CONVERT(char(6), RouteDate, 112) AS year_month,
    COALESCE(NULLIF(LTRIM(RTRIM(TerrRole)), ''), '(empty)') AS territory_role,
    COUNT_BIG(*) AS route_rows,
    COUNT_BIG(DISTINCT AGG_VISIT_ID) AS visits,
    COUNT_BIG(DISTINCT SHIP_TO) AS stores,
    COUNT_BIG(DISTINCT NULLIF(LTRIM(RTRIM(TerrExID)), '')) AS territory_ids,
    SUM(CASE WHEN NULLIF(LTRIM(RTRIM(TerrExID)), '') IS NULL THEN 1 ELSE 0 END) AS rows_without_territory_id
FROM dbo.Fact_Routes
WHERE RouteDate >= '$periodStartSql'
  AND RouteDate < '$periodEndSql'
GROUP BY
    CONVERT(char(6), RouteDate, 112),
    COALESCE(NULLIF(LTRIM(RTRIM(TerrRole)), ''), '(empty)')
ORDER BY year_month, territory_role;
"@
Export-AuditQuery -Name "profile_route_roles_$YearMonth" -Query $routeRolesQuery -CommandTimeout 600

$Manifest | Export-Csv -LiteralPath (Join-Path $OutputDirectory 'manifest.csv') -NoTypeInformation -Encoding UTF8

$failed = @($Manifest | Where-Object { $_.status -ne 'ok' })
$readme = @"
Расширенный read-only аудит KPI H&N

Сервер: $Server
База: $Database
Период профилирования: $YearMonth
Создано: $(Get-Date -Format 'dd.MM.yyyy HH:mm:ss')

Что проверено:
- таблицы, представления, процедуры, функции и триггеры;
- доступность SQL-кода объектов;
- синонимы и зависимости;
- поля и объекты по словам KPI, PICOS, OSA, TOP16, SCALE, TARGET;
- варианты расчёта OSA и TOP16 по ТТ за выбранный месяц;
- значения флага PICOS в OSA и Facing;
- справочники MUST/TOP;
- роли территорий в маршрутах.

Безопасность:
- бизнес-таблицы и представления только читаются;
- INSERT, UPDATE, DELETE, MERGE и изменение схемы HnnDW не выполняются;
- временные таблицы создаются только внутри SQL-сессии в tempdb и удаляются автоматически;
- ФИО, логины и строки отдельных сотрудников не выгружаются.

Ошибок: $($failed.Count)
Подробности: manifest.csv
"@
Set-Content -LiteralPath (Join-Path $OutputDirectory 'README.txt') -Value $readme -Encoding UTF8

$archivePath = Join-Path $OutputRoot "HN_KPI_Logic_Audit_${YearMonth}_$timestamp.zip"
$resolvedOutputRoot = [IO.Path]::GetFullPath($OutputRoot).TrimEnd('\')
$resolvedOutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$expectedPrefix = "$resolvedOutputRoot\"
if (
    -not $resolvedOutputDirectory.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase) -or
    -not ([IO.Path]::GetFileName($resolvedOutputDirectory)).StartsWith('HN_KPI_Logic_Audit_', [StringComparison]::OrdinalIgnoreCase)
) {
    throw "Отказ от упаковки и удаления неожиданной папки: $resolvedOutputDirectory"
}

Compress-Archive -Path (Join-Path $resolvedOutputDirectory '*') -DestinationPath $archivePath -Force
Remove-Item -LiteralPath $resolvedOutputDirectory -Recurse -Force

Write-Host ''
Write-Host "Аудит завершён. Архив: $archivePath"
Write-Host "Успешных блоков: $($Manifest.Count - $failed.Count) из $($Manifest.Count)"
if ($failed.Count -gt 0) {
    throw "Часть проверок завершилась ошибкой. Смотрите manifest.csv внутри архива."
}
