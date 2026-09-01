[CmdletBinding()]
param(
    [string]$Server = 'danoneDB.dedicorp.ru,1433',
    [string]$OutputRoot = [Environment]::GetFolderPath('Desktop'),
    [switch]$TrustServerCertificate,
    [switch]$ProfileBusinessData,
    [switch]$ProfileReferenceData
)

$ErrorActionPreference = 'Stop'

function New-ReadOnlyConnectionString {
    param(
        [Parameter(Mandatory)]
        [string]$DataSource,
        [Parameter(Mandatory)]
        [PSCredential]$Credential,
        [string]$Database,
        [bool]$TrustCertificate
    )

    $builder = [System.Data.SqlClient.SqlConnectionStringBuilder]::new()
    $builder['Data Source'] = $DataSource
    $builder['User ID'] = $Credential.UserName
    $builder['Password'] = $Credential.GetNetworkCredential().Password
    $builder['Integrated Security'] = $false
    $builder['Application Name'] = 'HN SQL Metadata Audit'
    $builder['Connect Timeout'] = 15
    $builder['Encrypt'] = $true
    $builder['TrustServerCertificate'] = $TrustCertificate
    $builder['ApplicationIntent'] = 'ReadOnly'
    if ($Database) {
        $builder['Initial Catalog'] = $Database
    }
    return $builder.ConnectionString
}

function Invoke-MetadataQuery {
    param(
        [Parameter(Mandatory)]
        [string]$ConnectionString,
        [Parameter(Mandatory)]
        [string]$Query,
        [int]$CommandTimeout = 60
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
        return (, $table)
    }
    finally {
        $command.Dispose()
        $connection.Dispose()
    }
}

function Export-QueryCsv {
    param(
        [Parameter(Mandatory)]
        [string]$ConnectionString,
        [Parameter(Mandatory)]
        [string]$Query,
        [Parameter(Mandatory)]
        [string]$Path,
        [int]$CommandTimeout = 180
    )

    $result = Invoke-MetadataQuery `
        -ConnectionString $ConnectionString `
        -Query $Query `
        -CommandTimeout $CommandTimeout
    $result | Export-Csv -LiteralPath $Path -NoTypeInformation -Encoding UTF8
}

function Get-CandidateCategories {
    param([string]$SearchText)

    $categories = [System.Collections.Generic.List[string]]::new()
    $text = $SearchText.ToLowerInvariant()
    if ($text -match 'kpi|picos|top.?16|(^|[^a-z0-9])osa([^a-z0-9]|$)|scale|bonus|target|цель|план') {
        $categories.Add('KPI')
    }
    if ($text -match 'employee|staff|person|user|login|account|сотруд|мерч|merch|supervisor|супервайзер|territor|руковод') {
        $categories.Add('Сотрудники и руководители')
    }
    if ($text -match 'visit|route|outlet|store|shop|point|address|sap|визит|маршрут|торгов|адрес') {
        $categories.Add('ТТ, маршруты и визиты')
    }
    if ($text -match 'network|chain|client|customer|сеть|клиент') {
        $categories.Add('Сети и клиенты')
    }
    return $categories
}

$userName = Read-Host 'Введите SQL-логин'
if ([string]::IsNullOrWhiteSpace($userName)) {
    throw 'SQL-логин не указан'
}
$securePassword = Read-Host 'Введите SQL-пароль' -AsSecureString
$credential = [PSCredential]::new($userName, $securePassword)

$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$outputDirectory = Join-Path $OutputRoot "HN_SQL_Audit_$timestamp"
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

$baseConnectionString = New-ReadOnlyConnectionString `
    -DataSource $Server `
    -Credential $credential `
    -TrustCertificate $TrustServerCertificate.IsPresent

$serverInfo = Invoke-MetadataQuery -ConnectionString $baseConnectionString -Query @'
SELECT
    CAST(SERVERPROPERTY('ServerName') AS nvarchar(256)) AS server_name,
    CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)) AS product_version,
    CAST(SERVERPROPERTY('Edition') AS nvarchar(256)) AS edition,
    DB_NAME() AS current_database,
    ORIGINAL_LOGIN() AS login_name,
    GETDATE() AS server_time;
'@

$databases = Invoke-MetadataQuery -ConnectionString $baseConnectionString -Query @'
SELECT
    name AS database_name,
    state_desc,
    compatibility_level,
    CAST(HAS_DBACCESS(name) AS int) AS has_access
FROM sys.databases
WHERE database_id > 4
  AND source_database_id IS NULL
  AND state_desc = 'ONLINE'
  AND HAS_DBACCESS(name) = 1
ORDER BY name;
'@

$allTables = [System.Collections.Generic.List[object]]::new()
$allColumns = [System.Collections.Generic.List[object]]::new()
$errors = [System.Collections.Generic.List[object]]::new()

foreach ($databaseRow in $databases.Rows) {
    $databaseName = [string]$databaseRow.database_name
    try {
        $databaseConnectionString = New-ReadOnlyConnectionString `
            -DataSource $Server `
            -Credential $credential `
            -Database $databaseName `
            -TrustCertificate $TrustServerCertificate.IsPresent

        $tables = Invoke-MetadataQuery -ConnectionString $databaseConnectionString -Query @'
SELECT
    DB_NAME() AS database_name,
    s.name AS schema_name,
    o.name AS table_name,
    CASE o.type WHEN 'U' THEN 'TABLE' WHEN 'V' THEN 'VIEW' END AS object_type,
    SUM(CASE WHEN o.type = 'U' AND p.index_id IN (0, 1) THEN p.rows ELSE 0 END) AS approximate_rows,
    o.create_date,
    o.modify_date
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
LEFT JOIN sys.partitions p ON p.object_id = o.object_id
WHERE o.type IN ('U', 'V')
  AND o.is_ms_shipped = 0
GROUP BY s.name, o.name, o.type, o.create_date, o.modify_date
ORDER BY s.name, o.name;
'@

        foreach ($row in $tables.Rows) {
            $allTables.Add([pscustomobject]@{
                database_name = [string]$row.database_name
                schema_name = [string]$row.schema_name
                table_name = [string]$row.table_name
                object_type = [string]$row.object_type
                approximate_rows = [long]$row.approximate_rows
                create_date = $row.create_date
                modify_date = $row.modify_date
            })
        }

        $columns = Invoke-MetadataQuery -ConnectionString $databaseConnectionString -Query @'
SELECT
    DB_NAME() AS database_name,
    s.name AS schema_name,
    o.name AS table_name,
    CASE o.type WHEN 'U' THEN 'TABLE' WHEN 'V' THEN 'VIEW' END AS object_type,
    c.column_id,
    c.name AS column_name,
    ty.name AS data_type,
    c.max_length,
    c.precision,
    c.scale,
    c.is_nullable
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
JOIN sys.columns c ON c.object_id = o.object_id
JOIN sys.types ty ON ty.user_type_id = c.user_type_id
WHERE o.type IN ('U', 'V')
  AND o.is_ms_shipped = 0
ORDER BY s.name, o.name, c.column_id;
'@

        foreach ($row in $columns.Rows) {
            $allColumns.Add([pscustomobject]@{
                database_name = [string]$row.database_name
                schema_name = [string]$row.schema_name
                table_name = [string]$row.table_name
                object_type = [string]$row.object_type
                column_id = [int]$row.column_id
                column_name = [string]$row.column_name
                data_type = [string]$row.data_type
                max_length = [int]$row.max_length
                precision = [int]$row.precision
                scale = [int]$row.scale
                is_nullable = [bool]$row.is_nullable
            })
        }
    }
    catch {
        $errors.Add([pscustomobject]@{
            database_name = $databaseName
            error = $_.Exception.Message
        })
    }
}

$candidateColumns = [System.Collections.Generic.List[object]]::new()
foreach ($column in $allColumns) {
    $searchText = "$($column.schema_name) $($column.table_name) $($column.column_name)"
    foreach ($category in (Get-CandidateCategories -SearchText $searchText)) {
        $candidateColumns.Add([pscustomobject]@{
            category = $category
            database_name = $column.database_name
            schema_name = $column.schema_name
            table_name = $column.table_name
            object_type = $column.object_type
            column_name = $column.column_name
            data_type = $column.data_type
        })
    }
}

$candidateTables = $candidateColumns |
    Group-Object database_name, schema_name, table_name |
    ForEach-Object {
        $first = $_.Group[0]
        $table = $allTables |
            Where-Object {
                $_.database_name -eq $first.database_name -and
                $_.schema_name -eq $first.schema_name -and
                $_.table_name -eq $first.table_name
            } |
            Select-Object -First 1
        [pscustomobject]@{
            database_name = $first.database_name
            schema_name = $first.schema_name
            table_name = $first.table_name
            object_type = $first.object_type
            categories = ($_.Group.category | Sort-Object -Unique) -join '; '
            matched_columns = ($_.Group.column_name | Sort-Object -Unique) -join '; '
            approximate_rows = $table.approximate_rows
            modify_date = $table.modify_date
        }
    } |
    Sort-Object database_name, schema_name, table_name

$serverInfo | Export-Csv -LiteralPath (Join-Path $outputDirectory 'server_info.csv') -NoTypeInformation -Encoding UTF8
$databases | Export-Csv -LiteralPath (Join-Path $outputDirectory 'databases.csv') -NoTypeInformation -Encoding UTF8
$allTables | Export-Csv -LiteralPath (Join-Path $outputDirectory 'objects.csv') -NoTypeInformation -Encoding UTF8
$allColumns | Export-Csv -LiteralPath (Join-Path $outputDirectory 'columns.csv') -NoTypeInformation -Encoding UTF8
$candidateColumns | Export-Csv -LiteralPath (Join-Path $outputDirectory 'candidate_columns.csv') -NoTypeInformation -Encoding UTF8
$candidateTables | Export-Csv -LiteralPath (Join-Path $outputDirectory 'candidate_tables.csv') -NoTypeInformation -Encoding UTF8
$errors | Export-Csv -LiteralPath (Join-Path $outputDirectory 'audit_errors.csv') -NoTypeInformation -Encoding UTF8

if ($ProfileBusinessData.IsPresent -and ($databases.Rows.database_name -contains 'HnnDW')) {
    $businessConnectionString = New-ReadOnlyConnectionString `
        -DataSource $Server `
        -Credential $credential `
        -Database 'HnnDW' `
        -TrustCertificate $TrustServerCertificate.IsPresent

    $profiles = @(
        @{
            Name = 'profile_date_coverage.csv'
            Query = @'
SELECT 'Fact_Visits' AS object_name, MIN(VISIT_DATE) AS min_date, MAX(VISIT_DATE) AS max_date, COUNT_BIG(*) AS row_count
FROM dbo.Fact_Visits
UNION ALL
SELECT 'Fact_Routes', MIN(RouteDate), MAX(RouteDate), COUNT_BIG(*)
FROM dbo.Fact_Routes
UNION ALL
SELECT 'FACT_PICOS', MIN(VISIT_DATE), MAX(VISIT_DATE), COUNT_BIG(*)
FROM dbo.FACT_PICOS;
'@
        },
        @{
            Name = 'profile_agent_keys.csv'
            Query = @'
SELECT
    COUNT_BIG(*) AS agents,
    COUNT_BIG(DISTINCT Masterfid) AS distinct_masterfid,
    SUM(CASE WHEN NULLIF(LTRIM(RTRIM(Exid)), '') IS NOT NULL THEN 1 ELSE 0 END) AS agents_with_exid,
    COUNT_BIG(DISTINCT NULLIF(LTRIM(RTRIM(Exid)), '')) AS distinct_exid,
    SUM(CASE WHEN Activeflag = 1 THEN 1 ELSE 0 END) AS active_agents,
    SUM(CASE WHEN Activeflag = 1 AND NULLIF(LTRIM(RTRIM(Exid)), '') IS NOT NULL THEN 1 ELSE 0 END) AS active_agents_with_exid
FROM dbo.DIM_Agents;
'@
        },
        @{
            Name = 'profile_visit_key_coverage.csv'
            CommandTimeout = 300
            Query = @'
WITH agents AS (
    SELECT Masterfid, MAX(CASE WHEN NULLIF(LTRIM(RTRIM(Exid)), '') IS NOT NULL THEN 1 ELSE 0 END) AS has_exid
    FROM dbo.DIM_Agents
    GROUP BY Masterfid
),
stores AS (
    SELECT DISTINCT SHIP_TO
    FROM dbo.Dim_POSList
    WHERE NULLIF(LTRIM(RTRIM(SHIP_TO)), '') IS NOT NULL
),
routes AS (
    SELECT DISTINCT AGG_VISIT_ID, SHIP_TO
    FROM dbo.Fact_Routes
    WHERE RouteDate >= '2026-01-01' AND RouteDate < '2026-09-01'
)
SELECT
    YEAR(v.VISIT_DATE) * 100 + MONTH(v.VISIT_DATE) AS year_month,
    COUNT_BIG(*) AS visits,
    SUM(CASE WHEN v.VISIT_IS_COMPLETE = 1 AND v.PLACE_IS_CONFIRMED = 1 AND ISNULL(v.isInvalid, 0) = 0 THEN 1 ELSE 0 END) AS valid_visits,
    SUM(CASE WHEN v.Masterfid IS NOT NULL THEN 1 ELSE 0 END) AS visits_with_masterfid,
    SUM(CASE WHEN a.Masterfid IS NOT NULL THEN 1 ELSE 0 END) AS visits_with_agent,
    SUM(CASE WHEN a.has_exid = 1 THEN 1 ELSE 0 END) AS visits_with_agent_exid,
    SUM(CASE WHEN s.SHIP_TO IS NOT NULL THEN 1 ELSE 0 END) AS visits_with_store,
    SUM(CASE WHEN r.AGG_VISIT_ID IS NOT NULL THEN 1 ELSE 0 END) AS visits_with_route
FROM dbo.Fact_Visits v
LEFT JOIN agents a ON a.Masterfid = v.Masterfid
LEFT JOIN stores s ON s.SHIP_TO = v.SHIP_TO
LEFT JOIN routes r ON r.AGG_VISIT_ID = v.AGG_VISIT_ID AND r.SHIP_TO = v.SHIP_TO
WHERE v.VISIT_DATE >= '2026-01-01' AND v.VISIT_DATE < '2026-09-01'
GROUP BY YEAR(v.VISIT_DATE) * 100 + MONTH(v.VISIT_DATE)
ORDER BY year_month;
'@
        },
        @{
            Name = 'profile_territory_roles.csv'
            CommandTimeout = 240
            Query = @'
SELECT
    YEAR(RouteDate) * 100 + MONTH(RouteDate) AS year_month,
    COALESCE(NULLIF(LTRIM(RTRIM(TerrRole)), ''), '(empty)') AS territory_role,
    COUNT_BIG(*) AS route_rows,
    COUNT_BIG(DISTINCT AGG_VISIT_ID) AS visits,
    COUNT_BIG(DISTINCT NULLIF(LTRIM(RTRIM(TerrExID)), '')) AS territory_ids,
    SUM(CASE WHEN NULLIF(LTRIM(RTRIM(TerrExID)), '') IS NULL THEN 1 ELSE 0 END) AS rows_without_territory_id
FROM dbo.Fact_Routes
WHERE RouteDate >= '2026-01-01' AND RouteDate < '2026-09-01'
GROUP BY
    YEAR(RouteDate) * 100 + MONTH(RouteDate),
    COALESCE(NULLIF(LTRIM(RTRIM(TerrRole)), ''), '(empty)')
ORDER BY year_month, territory_role;
'@
        },
        @{
            Name = 'profile_picos_coverage.csv'
            CommandTimeout = 240
            Query = @'
SELECT
    YEAR(VISIT_DATE) * 100 + MONTH(VISIT_DATE) AS year_month,
    COUNT_BIG(*) AS source_rows,
    COUNT_BIG(DISTINCT VISIT_ID) AS visits,
    COUNT_BIG(DISTINCT SHIP_TO) AS stores,
    SUM(CASE WHEN PICOS_SCORE_FACT IS NOT NULL THEN 1 ELSE 0 END) AS rows_with_fact,
    SUM(CASE WHEN PICOS_TARGET_SCORE > 0 THEN 1 ELSE 0 END) AS rows_with_target,
    SUM(CASE WHEN PICOS_POTENTIAL_SCORE > 0 THEN 1 ELSE 0 END) AS rows_with_potential,
    SUM(CASE WHEN PICOS_SCORE_FACT IS NOT NULL AND PICOS_TARGET_SCORE > 0 THEN 1 ELSE 0 END) AS rows_with_fact_and_target
FROM dbo.FACT_PICOS
WHERE VISIT_DATE >= '2026-01-01' AND VISIT_DATE < '2026-09-01'
GROUP BY YEAR(VISIT_DATE) * 100 + MONTH(VISIT_DATE)
ORDER BY year_month;
'@
        },
        @{
            Name = 'profile_musttop_lists.csv'
            Query = @'
SELECT
    COALESCE(NULLIF(LTRIM(RTRIM(ListType)), ''), '(empty)') AS list_type,
    COALESCE(NULLIF(LTRIM(RTRIM(MustTopName)), ''), '(empty)') AS list_name,
    COUNT_BIG(DISTINCT MustTopID) AS list_ids,
    COUNT_BIG(DISTINCT PRODUCT_NATURE_CD) AS products,
    MIN(StartDate) AS min_start_date,
    MAX(EndDate) AS max_end_date
FROM dbo.V_FACT_Items_MustTop
GROUP BY
    COALESCE(NULLIF(LTRIM(RTRIM(ListType)), ''), '(empty)'),
    COALESCE(NULLIF(LTRIM(RTRIM(MustTopName)), ''), '(empty)')
ORDER BY list_type, list_name;
'@
        },
        @{
            Name = 'profile_top16_store_assignment_202607.csv'
            CommandTimeout = 240
            Query = @'
WITH active_store_lists AS (
    SELECT DISTINCT SHIP_TO, MustTopID
    FROM dbo.V_FACT_Faces_MustTop
    WHERE StartDate < '2026-08-01'
      AND (EndDate IS NULL OR EndDate >= '2026-07-01')
),
active_item_lists AS (
    SELECT DISTINCT MustTopID, COALESCE(NULLIF(LTRIM(RTRIM(ListType)), ''), '(empty)') AS ListType
    FROM dbo.V_FACT_Items_MustTop
    WHERE StartDate < '2026-08-01'
      AND (EndDate IS NULL OR EndDate >= '2026-07-01')
),
stores AS (
    SELECT SHIP_TO, MAX(CLIENT) AS network_code, MAX(ClientName) AS network_name
    FROM dbo.Dim_POSList
    GROUP BY SHIP_TO
)
SELECT
    COALESCE(NULLIF(LTRIM(RTRIM(s.network_code)), ''), '(empty)') AS network_code,
    COALESCE(NULLIF(LTRIM(RTRIM(s.network_name)), ''), '(empty)') AS network_name,
    i.ListType AS list_type,
    COUNT_BIG(DISTINCT f.SHIP_TO) AS stores,
    COUNT_BIG(DISTINCT f.MustTopID) AS list_ids
FROM active_store_lists f
JOIN active_item_lists i ON i.MustTopID = f.MustTopID
LEFT JOIN stores s ON s.SHIP_TO = f.SHIP_TO
GROUP BY
    COALESCE(NULLIF(LTRIM(RTRIM(s.network_code)), ''), '(empty)'),
    COALESCE(NULLIF(LTRIM(RTRIM(s.network_name)), ''), '(empty)'),
    i.ListType
ORDER BY stores DESC, network_code, list_type;
'@
        },
        @{
            Name = 'profile_osa_202607.csv'
            CommandTimeout = 300
            Query = @'
SELECT
    COALESCE(NULLIF(LTRIM(RTRIM(MUST)), ''), '(empty)') AS must_value,
    COUNT_BIG(*) AS source_rows,
    COUNT_BIG(DISTINCT VISIT_ID) AS visits,
    COUNT_BIG(DISTINCT fid) AS stores,
    COUNT_BIG(DISTINCT iid) AS products,
    SUM(CASE WHEN IN_MATRIX = 1 THEN 1 ELSE 0 END) AS rows_in_matrix,
    SUM(CASE WHEN IN_MATRIX = 1 AND IN_STOCK = 1 THEN 1 ELSE 0 END) AS rows_in_stock
FROM dbo.Fact_OSA_202607
GROUP BY COALESCE(NULLIF(LTRIM(RTRIM(MUST)), ''), '(empty)')
ORDER BY source_rows DESC;
'@
        }
    )

    foreach ($profile in $profiles) {
        try {
            $timeout = if ($profile.ContainsKey('CommandTimeout')) { [int]$profile.CommandTimeout } else { 180 }
            Export-QueryCsv `
                -ConnectionString $businessConnectionString `
                -Query $profile.Query `
                -Path (Join-Path $outputDirectory $profile.Name) `
                -CommandTimeout $timeout
        }
        catch {
            $errors.Add([pscustomobject]@{
                database_name = 'HnnDW'
                error = "$($profile.Name): $($_.Exception.Message)"
            })
        }
    }

    $errors | Export-Csv -LiteralPath (Join-Path $outputDirectory 'audit_errors.csv') -NoTypeInformation -Encoding UTF8
}

if ($ProfileReferenceData.IsPresent -and ($databases.Rows.database_name -contains 'HnnDW')) {
    $referenceConnectionString = New-ReadOnlyConnectionString `
        -DataSource $Server `
        -Credential $credential `
        -Database 'HnnDW' `
        -TrustCertificate $TrustServerCertificate.IsPresent

    $referenceProfiles = @(
        @{
            Name = 'profile_attribute_names.csv'
            Query = @'
SELECT
    AttrName,
    AttrExid,
    AttrTypeID,
    COUNT_BIG(*) AS value_rows,
    COUNT_BIG(DISTINCT AttrValueID) AS attribute_values
FROM dbo.DIM_Attributes
GROUP BY AttrName, AttrExid, AttrTypeID
ORDER BY AttrName, AttrExid;
'@
        },
        @{
            Name = 'profile_kpi_attribute_values.csv'
            Query = @'
SELECT
    AttrID,
    AttrName,
    AttrExid,
    AttrValueID,
    AttrValueName,
    AttrvalueExid,
    AttrTypeID
FROM dbo.DIM_Attributes
WHERE LOWER(CONCAT(AttrName, ' ', AttrExid, ' ', AttrValueName, ' ', AttrvalueExid)) LIKE '%kpi%'
   OR LOWER(CONCAT(AttrName, ' ', AttrExid, ' ', AttrValueName, ' ', AttrvalueExid)) LIKE '%picos%'
   OR LOWER(CONCAT(AttrName, ' ', AttrExid, ' ', AttrValueName, ' ', AttrvalueExid)) LIKE '%osa%'
   OR LOWER(CONCAT(AttrName, ' ', AttrExid, ' ', AttrValueName, ' ', AttrvalueExid)) LIKE '%top%'
   OR LOWER(CONCAT(AttrName, ' ', AttrExid, ' ', AttrValueName, ' ', AttrvalueExid)) LIKE '%target%'
   OR LOWER(CONCAT(AttrName, ' ', AttrExid, ' ', AttrValueName, ' ', AttrvalueExid)) LIKE '%цель%'
ORDER BY AttrName, AttrValueName;
'@
        },
        @{
            Name = 'profile_matrix_names.csv'
            Query = @'
SELECT
    Matrixid,
    Matrixname,
    MatrixExid,
    Activeflag,
    MatrixDelDatetime
FROM dbo.DIM_Matrix
WHERE LOWER(CONCAT(Matrixname, ' ', MatrixExid)) LIKE '%kpi%'
   OR LOWER(CONCAT(Matrixname, ' ', MatrixExid)) LIKE '%picos%'
   OR LOWER(CONCAT(Matrixname, ' ', MatrixExid)) LIKE '%osa%'
   OR LOWER(CONCAT(Matrixname, ' ', MatrixExid)) LIKE '%top%'
   OR LOWER(CONCAT(Matrixname, ' ', MatrixExid)) LIKE '%target%'
ORDER BY Matrixname;
'@
        },
        @{
            Name = 'profile_sql_modules.csv'
            Query = @'
SELECT
    s.name AS schema_name,
    o.name AS object_name,
    o.type_desc,
    m.definition
FROM sys.sql_modules m
JOIN sys.objects o ON o.object_id = m.object_id
JOIN sys.schemas s ON s.schema_id = o.schema_id
WHERE LOWER(m.definition) LIKE '%picos%'
   OR LOWER(m.definition) LIKE '%musttop%'
   OR LOWER(m.definition) LIKE '%in_stock%'
   OR LOWER(m.definition) LIKE '%group_fact%'
   OR LOWER(m.definition) LIKE '%facing_fact%'
ORDER BY s.name, o.name;
'@
        },
        @{
            Name = 'profile_survey_kpi_questions.csv'
            Query = @'
SELECT
    q.QuestionID,
    q.QuestionName,
    q.Survey_Agency_Name,
    s.SectionName
FROM dbo.Dim_Survey_Questions q
LEFT JOIN dbo.DIM_Survey_Sections s ON s.SectionID = q.SectionID
WHERE LOWER(CONCAT(q.QuestionName, ' ', s.SectionName)) LIKE '%kpi%'
   OR LOWER(CONCAT(q.QuestionName, ' ', s.SectionName)) LIKE '%picos%'
   OR LOWER(CONCAT(q.QuestionName, ' ', s.SectionName)) LIKE '%osa%'
   OR LOWER(CONCAT(q.QuestionName, ' ', s.SectionName)) LIKE '%top%'
   OR LOWER(CONCAT(q.QuestionName, ' ', s.SectionName)) LIKE '%target%'
   OR LOWER(CONCAT(q.QuestionName, ' ', s.SectionName)) LIKE '%цель%'
ORDER BY q.QuestionName;
'@
        }
    )

    foreach ($profile in $referenceProfiles) {
        try {
            Export-QueryCsv `
                -ConnectionString $referenceConnectionString `
                -Query $profile.Query `
                -Path (Join-Path $outputDirectory $profile.Name) `
                -CommandTimeout 180
        }
        catch {
            $errors.Add([pscustomobject]@{
                database_name = 'HnnDW'
                error = "$($profile.Name): $($_.Exception.Message)"
            })
        }
    }

    $errors | Export-Csv -LiteralPath (Join-Path $outputDirectory 'audit_errors.csv') -NoTypeInformation -Encoding UTF8
}

$summary = @(
    "SQL metadata audit",
    "Server: $Server",
    "Created: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    "Accessible databases: $($databases.Rows.Count)",
    "Tables and views: $($allTables.Count)",
    "Columns: $($allColumns.Count)",
    "Candidate tables: $(@($candidateTables).Count)",
    "Database errors: $($errors.Count)",
    '',
    $(if ($ProfileBusinessData.IsPresent -or $ProfileReferenceData.IsPresent) {
        'Дополнительно выгружены только агрегаты покрытия и качества ключей без ФИО и строк отдельных сотрудников.'
    } else {
        'Аудит содержит только метаданные. Строки бизнес-таблиц и персональные данные не выгружались.'
    })
)
$summary | Set-Content -LiteralPath (Join-Path $outputDirectory 'README.txt') -Encoding UTF8

$archivePath = "$outputDirectory.zip"
Compress-Archive -Path (Join-Path $outputDirectory '*') -DestinationPath $archivePath -Force

Write-Host "Аудит завершён: $archivePath"
Write-Host 'Передайте ZIP-файл на локальный компьютер для анализа.'
