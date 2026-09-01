$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSEdition -ne "Core") {
    $pwsh = Get-Command pwsh -ErrorAction Stop
    & $pwsh.Source -NoProfile -File $PSCommandPath
    exit $LASTEXITCODE
}

$workspaceProcess = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -eq "msmdsrv.exe" -and $_.CommandLine -match "AnalysisServicesWorkspace" } |
    Select-Object -First 1
$connectionInfo = Get-NetTCPConnection -OwningProcess $workspaceProcess.ProcessId -State Listen |
    Where-Object { $_.LocalAddress -in @("127.0.0.1", "::1") } |
    Select-Object -First 1
if (-not $workspaceProcess -or -not $connectionInfo) { throw "Открытая модель Power BI Desktop не найдена." }

$clientPath = Resolve-Path ".tmp_nuget/adomd/lib/netcoreapp3.0/Microsoft.AnalysisServices.AdomdClient.dll"
Add-Type -Path $clientPath

function Invoke-Scalar([string]$ConnectionString, [string]$Query) {
    $connection = [Microsoft.AnalysisServices.AdomdClient.AdomdConnection]::new(
        $ConnectionString
    )
    $connection.Open()
    try {
        $command = $connection.CreateCommand()
        $command.CommandText = $Query
        $reader = $command.ExecuteReader()
        [void]$reader.Read()
        return [int]$reader.GetValue(0)
    }
    finally {
        if ($reader) { $reader.Close() }
        $connection.Close()
    }
}

$baseConnection = "Data Source=localhost:$($connectionInfo.LocalPort)"
$securityRows = Invoke-Scalar $baseConnection 'EVALUATE ROW("Rows", COUNTROWS(FILTER(''dim_employees'', NOT ISBLANK(''dim_employees''[Электронная почта]) && ''dim_employees''[Активен] = TRUE())))'
$visibleRegions = Invoke-Scalar "$baseConnection;Roles=RegionSecurity" 'EVALUATE ROW("Regions", COUNTROWS(''dRegion''))'
Write-Output "Active employees with email: $securityRows"
Write-Output "Regions for current Windows identity under RLS: $visibleRegions"
if ($securityRows -lt 1) { throw "В dim_employees нет активных сотрудников с email для RLS." }
