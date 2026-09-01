param(
    [string]$PlanPath = "reports/powerbi_cleanup_plan.json",
    [switch]$Apply
)

$ErrorActionPreference = "Stop"

if ($PSVersionTable.PSEdition -ne "Core") {
    $pwsh = Get-Command pwsh -ErrorAction Stop
    $arguments = @("-NoProfile", "-File", $PSCommandPath, "-PlanPath", $PlanPath)
    if ($Apply) { $arguments += "-Apply" }
    & $pwsh.Source @arguments
    exit $LASTEXITCODE
}

$workspaceProcess = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -eq "msmdsrv.exe" -and $_.CommandLine -match "AnalysisServicesWorkspace" } |
    Select-Object -First 1
if (-not $workspaceProcess) { throw "Открытая модель Power BI Desktop не найдена." }

$connectionInfo = Get-NetTCPConnection -OwningProcess $workspaceProcess.ProcessId -State Listen |
    Where-Object { $_.LocalAddress -in @("127.0.0.1", "::1") } |
    Select-Object -First 1
if (-not $connectionInfo) { throw "Не удалось определить локальный порт Power BI Desktop." }

$tomDirectory = Resolve-Path ".tmp_nuget/tom/lib/netcoreapp3.0"
foreach ($assembly in @(
    "Microsoft.AnalysisServices.Runtime.Core.dll",
    "Microsoft.AnalysisServices.Runtime.Windows.dll",
    "Microsoft.AnalysisServices.Core.dll",
    "Microsoft.AnalysisServices.dll",
    "Microsoft.AnalysisServices.Tabular.dll"
)) {
    Add-Type -Path (Join-Path $tomDirectory $assembly)
}

$plan = Get-Content $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
$server = [Microsoft.AnalysisServices.Tabular.Server]::new()
$server.Connect("localhost:$($connectionInfo.LocalPort)")

try {
    $database = $server.Databases | Select-Object -First 1
    if (-not $database) { throw "База данных Power BI не найдена." }
    $model = $database.Model
    $actions = [System.Collections.Generic.List[string]]::new()

    if ($plan.required_measures) {
        foreach ($tableName in $plan.required_measures.PSObject.Properties.Name) {
            $table = $model.Tables.Find($tableName)
            if (-not $table) { throw "Не найдена таблица мер: $tableName" }
            foreach ($definition in @($plan.required_measures.$tableName)) {
                $measure = $table.Measures.Find($definition.name)
                if (-not $measure) {
                    $actions.Add("Обязательная мера: $tableName[$($definition.name)]")
                    if ($Apply) {
                        $measure = [Microsoft.AnalysisServices.Tabular.Measure]::new()
                        $measure.Name = $definition.name
                        $measure.Expression = $definition.expression
                        if ($definition.format_string) {
                            $measure.FormatString = $definition.format_string
                        }
                        [void]$table.Measures.Add($measure)
                    }
                }
            }
        }
    }

    foreach ($measureName in $plan.measures) {
        foreach ($table in @($model.Tables)) {
            $measure = $table.Measures.Find($measureName)
            if ($measure) {
                $actions.Add("Мера: $($table.Name)[$measureName]")
                if ($Apply) { [void]$table.Measures.Remove($measure) }
            }
        }
    }

    foreach ($tableName in $plan.columns.PSObject.Properties.Name) {
        $table = $model.Tables.Find($tableName)
        if (-not $table) { continue }
        $columnsToRemove = @($plan.columns.$tableName)
        foreach ($relationship in @($model.Relationships)) {
            $fromRemoved = (
                $relationship.FromTable.Name -eq $tableName -and
                $relationship.FromColumn.Name -in $columnsToRemove
            )
            $toRemoved = (
                $relationship.ToTable.Name -eq $tableName -and
                $relationship.ToColumn.Name -in $columnsToRemove
            )
            if ($fromRemoved -or $toRemoved) {
                $actions.Add("Связь с удаляемой колонкой: $($relationship.Name)")
                if ($Apply) { [void]$model.Relationships.Remove($relationship) }
            }
        }
        foreach ($columnName in $columnsToRemove) {
            $column = $table.Columns.Find($columnName)
            if ($column) {
                $actions.Add("Колонка: $tableName[$columnName]")
                if ($Apply) { [void]$table.Columns.Remove($column) }
            }
        }
    }

    $tablesToHide = if ($plan.hide_tables) { $plan.hide_tables } else { $plan.tables }
    foreach ($tableName in $tablesToHide) {
        $table = $model.Tables.Find($tableName)
        if (-not $table) { continue }
        $actions.Add("Скрытая таблица: $tableName")
        if ($Apply) { $table.IsHidden = $true }
    }

    if ($Apply) {
        $model.SaveChanges()
        Write-Output "Изменения применены: $($actions.Count) объектов. Сохраните PBIX через Ctrl+S."
    } else {
        Write-Output "Предварительный просмотр: $($actions.Count) объектов."
    }
    $actions | Set-Content "reports/powerbi_cleanup_actions.txt" -Encoding UTF8
    $actions | Select-Object -First 40
}
finally {
    $server.Disconnect()
}
