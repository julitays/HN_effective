param(
    [string]$MetadataDirectory = "reports/powerbi_model_metadata",
    [string]$OutputFile = "reports/powerbi_model_table_profiles.csv"
)

$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSEdition -ne "Core") {
    $pwsh = Get-Command pwsh -ErrorAction Stop
    & $pwsh.Source -NoProfile -File $PSCommandPath `
        -MetadataDirectory $MetadataDirectory `
        -OutputFile $OutputFile
    exit $LASTEXITCODE
}

$workspaceProcess = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -eq "msmdsrv.exe" -and $_.CommandLine -match "AnalysisServicesWorkspace" } |
    Select-Object -First 1
$connectionInfo = Get-NetTCPConnection -OwningProcess $workspaceProcess.ProcessId -State Listen |
    Where-Object { $_.LocalAddress -in @("127.0.0.1", "::1") } |
    Select-Object -First 1

if (-not $workspaceProcess -or -not $connectionInfo) {
    throw "Открытая модель Power BI Desktop не найдена."
}

$clientPath = Resolve-Path ".tmp_nuget/adomd/lib/netcoreapp3.0/Microsoft.AnalysisServices.AdomdClient.dll"
Add-Type -Path $clientPath

$tables = Import-Csv (Join-Path $MetadataDirectory "tables.csv")
$columns = Import-Csv (Join-Path $MetadataDirectory "columns.csv")
$tableNames = @{}
foreach ($table in $tables) { $tableNames[$table.ID] = $table.Name }
$monthTables = @{}
foreach ($column in $columns) {
    $columnName = if ($column.ExplicitName) { $column.ExplicitName } else { $column.InferredName }
    if ($columnName -eq "MonthStart") { $monthTables[$tableNames[$column.TableID]] = $true }
}

$parquetTables = @{}
Get-ChildItem -Path "data/out" -Filter "*.parquet" -File | ForEach-Object {
    $parquetTables[$_.BaseName] = $true
}
$targetTables = $tables |
    Where-Object { $parquetTables.ContainsKey($_.Name) } |
    Sort-Object Name

$connection = [Microsoft.AnalysisServices.AdomdClient.AdomdConnection]::new(
    "Data Source=localhost:$($connectionInfo.LocalPort)"
)
$connection.Open()
$profiles = [System.Collections.Generic.List[object]]::new()

try {
    foreach ($table in $targetTables) {
        $escapedTable = $table.Name.Replace("'", "''")
        $monthExpression = if ($monthTables.ContainsKey($table.Name)) {
            ", `"Последний период`", MAX('$escapedTable'[MonthStart])"
        } else { "" }
        $command = $connection.CreateCommand()
        $command.CommandText = "EVALUATE ROW(`"Строк`", COUNTROWS('$escapedTable')$monthExpression)"
        $reader = $command.ExecuteReader()
        if ($reader.Read()) {
            $profiles.Add(
                [pscustomobject]@{
                    "Таблица" = $table.Name
                    "Строк в модели" = $reader.GetValue(0)
                    "Последний период модели" = if ($reader.FieldCount -gt 1 -and -not $reader.IsDBNull(1)) { $reader.GetValue(1) } else { $null }
                }
            )
        }
        $reader.Close()
    }
}
finally {
    $connection.Close()
}

$profiles | Export-Csv -Path $OutputFile -NoTypeInformation -Encoding UTF8
Write-Output "Сохранено: $OutputFile ($($profiles.Count) таблиц)"
