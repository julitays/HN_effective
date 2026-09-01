param(
    [string]$OutputDirectory = "reports/powerbi_model_metadata"
)

$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSEdition -ne "Core") {
    $pwsh = Get-Command pwsh -ErrorAction Stop
    & $pwsh.Source -NoProfile -File $PSCommandPath -OutputDirectory $OutputDirectory
    exit $LASTEXITCODE
}

$workspaceProcess = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -eq "msmdsrv.exe" -and $_.CommandLine -match "AnalysisServicesWorkspace" } |
    Select-Object -First 1

if (-not $workspaceProcess) {
    throw "Открытая модель Power BI Desktop не найдена."
}

$connectionInfo = Get-NetTCPConnection -OwningProcess $workspaceProcess.ProcessId -State Listen |
    Where-Object { $_.LocalAddress -in @("127.0.0.1", "::1") } |
    Select-Object -First 1

if (-not $connectionInfo) {
    throw "Не удалось определить локальный порт модели Power BI Desktop."
}

$clientPath = Resolve-Path ".tmp_nuget/adomd/lib/netcoreapp3.0/Microsoft.AnalysisServices.AdomdClient.dll"
Add-Type -Path $clientPath

$outputPath = New-Item -ItemType Directory -Force -Path $OutputDirectory
$connection = [Microsoft.AnalysisServices.AdomdClient.AdomdConnection]::new(
    "Data Source=localhost:$($connectionInfo.LocalPort)"
)
$connection.Open()

function Export-DmvQuery {
    param(
        [string]$Name,
        [string]$Query
    )

    $command = $connection.CreateCommand()
    $command.CommandText = $Query
    $reader = $command.ExecuteReader()
    $rows = [System.Collections.Generic.List[object]]::new()

    while ($reader.Read()) {
        $values = [ordered]@{}
        for ($index = 0; $index -lt $reader.FieldCount; $index++) {
            $value = $reader.GetValue($index)
            $values[$reader.GetName($index)] = if ($value -is [DBNull]) { $null } else { $value }
        }
        $rows.Add([pscustomobject]$values)
    }
    $reader.Close()
    $rows | Export-Csv -Path (Join-Path $outputPath "$Name.csv") -NoTypeInformation -Encoding UTF8
    Write-Output "${Name}: $($rows.Count) строк"
}

try {
    Export-DmvQuery "tables" 'SELECT * FROM $SYSTEM.TMSCHEMA_TABLES'
    Export-DmvQuery "columns" 'SELECT * FROM $SYSTEM.TMSCHEMA_COLUMNS'
    Export-DmvQuery "measures" 'SELECT * FROM $SYSTEM.TMSCHEMA_MEASURES'
    Export-DmvQuery "relationships" 'SELECT * FROM $SYSTEM.TMSCHEMA_RELATIONSHIPS'
    Export-DmvQuery "roles" 'SELECT * FROM $SYSTEM.TMSCHEMA_ROLES'
    Export-DmvQuery "table_permissions" 'SELECT * FROM $SYSTEM.TMSCHEMA_TABLE_PERMISSIONS'
    Export-DmvQuery "partitions" 'SELECT * FROM $SYSTEM.TMSCHEMA_PARTITIONS'
    Export-DmvQuery "calculation_dependencies" 'SELECT * FROM $SYSTEM.DISCOVER_CALC_DEPENDENCY'
    Export-DmvQuery "storage_table_columns" 'SELECT * FROM $SYSTEM.DISCOVER_STORAGE_TABLE_COLUMNS'
    Export-DmvQuery "storage_column_segments" 'SELECT * FROM $SYSTEM.DISCOVER_STORAGE_TABLE_COLUMN_SEGMENTS'
}
finally {
    $connection.Close()
}
