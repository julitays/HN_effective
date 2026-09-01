param([switch]$Apply)

$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSEdition -ne "Core") {
    $pwsh = Get-Command pwsh -ErrorAction Stop
    $arguments = @("-NoProfile", "-File", $PSCommandPath)
    if ($Apply) { $arguments += "-Apply" }
    & $pwsh.Source @arguments
    exit $LASTEXITCODE
}

$workspaceProcess = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -eq "msmdsrv.exe" -and $_.CommandLine -match "AnalysisServicesWorkspace" } |
    Select-Object -First 1
$connectionInfo = Get-NetTCPConnection -OwningProcess $workspaceProcess.ProcessId -State Listen |
    Where-Object { $_.LocalAddress -in @("127.0.0.1", "::1") } |
    Select-Object -First 1
if (-not $workspaceProcess -or -not $connectionInfo) { throw "Открытая модель Power BI Desktop не найдена." }

$tomDirectory = Resolve-Path ".tmp_nuget/tom/lib/netcoreapp3.0"
foreach ($assembly in @(
    "Microsoft.AnalysisServices.Runtime.Core.dll",
    "Microsoft.AnalysisServices.Runtime.Windows.dll",
    "Microsoft.AnalysisServices.Core.dll",
    "Microsoft.AnalysisServices.dll",
    "Microsoft.AnalysisServices.Tabular.dll"
)) { Add-Type -Path (Join-Path $tomDirectory $assembly) }

$server = [Microsoft.AnalysisServices.Tabular.Server]::new()
$server.Connect("localhost:$($connectionInfo.LocalPort)")
try {
    $database = $server.Databases | Select-Object -First 1
    $model = $database.Model
    $roleName = "RegionSecurity"
    $employeeTable = $model.Tables.Find("dim_employees")
    if (-not $employeeTable) { throw "В модели нет dim_employees для настройки RLS." }
    $existingRole = $model.Roles.Find($roleName)
    Write-Output "Источник RLS: dim_employees"
    Write-Output "Роль RLS: $(if ($existingRole) {'есть'} else {'будет создана'})"
    if (-not $Apply) { return }

    if (-not $existingRole) {
        $role = [Microsoft.AnalysisServices.Tabular.ModelRole]::new()
        $role.Name = $roleName
        $role.ModelPermission = [Microsoft.AnalysisServices.Tabular.ModelPermission]::Read
        [void]$model.Roles.Add($role)
        $existingRole = $role
    }

    $regionTable = $model.Tables.Find("dRegion")
    if (-not $regionTable) { throw "В модели нет dRegion." }
    $permission = $existingRole.TablePermissions.Find("dRegion")
    if (-not $permission) {
        $permission = [Microsoft.AnalysisServices.Tabular.TablePermission]::new()
        $permission.Table = $regionTable
        [void]$existingRole.TablePermissions.Add($permission)
    }
    $permission.FilterExpression = @"
VAR CurrentUPN = LOWER(USERPRINCIPALNAME())
VAR CurrentRegion = 'dRegion'[Регион BI]
RETURN
    COUNTROWS(
        FILTER(
            'dim_employees',
            LOWER('dim_employees'[Электронная почта]) = CurrentUPN
                && 'dim_employees'[Регион BI] = CurrentRegion
                && 'dim_employees'[Активен] = TRUE()
        )
    ) > 0
"@
    $employeePermission = $existingRole.TablePermissions.Find("dim_employees")
    if (-not $employeePermission) {
        $employeePermission = [Microsoft.AnalysisServices.Tabular.TablePermission]::new()
        $employeePermission.Table = $employeeTable
        [void]$existingRole.TablePermissions.Add($employeePermission)
    }
    $employeePermission.FilterExpression = @"
LOWER('dim_employees'[Электронная почта]) = LOWER(USERPRINCIPALNAME())
    && 'dim_employees'[Активен] = TRUE()
"@
    $employeeTable.IsHidden = $true
    $model.SaveChanges()
    Write-Output "RLS применён через активных сотрудников USERS. Неизвестный UPN получает ноль регионов."
}
finally {
    $server.Disconnect()
}
