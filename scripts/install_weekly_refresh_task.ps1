[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$At,
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$ReminderAt = '12:00',
    [string]$TransferRoot = 'H:\',
    [string]$TaskName = 'HN Weekly Dashboard ETL',
    [string]$ReminderTaskName = 'HN RDP KPI Reminder'
)

$ErrorActionPreference = 'Stop'
$Runner = Join-Path $PSScriptRoot 'run_weekly_refresh.ps1'
if (-not (Test-Path -LiteralPath $Runner)) {
    throw "Не найден сценарий обновления: $Runner"
}
$Reminder = Join-Path $PSScriptRoot 'show_rdp_reminder.ps1'
if (-not (Test-Path -LiteralPath $Reminder)) {
    throw "Не найден сценарий напоминания: $Reminder"
}

$startTime = [datetime]::ParseExact(
    $At,
    'HH:mm',
    [Globalization.CultureInfo]::InvariantCulture
)
$reminderTime = [datetime]::ParseExact(
    $ReminderAt,
    'HH:mm',
    [Globalization.CultureInfo]::InvariantCulture
)
$powerShellPath = (Get-Command powershell.exe -ErrorAction Stop).Source
$arguments = @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', ('"{0}"' -f $Runner),
    '-TransferRoot', ('"{0}"' -f $TransferRoot)
) -join ' '

$action = New-ScheduledTaskAction -Execute $powerShellPath -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At $startTime
$principal = New-ScheduledTaskPrincipal `
    -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description 'Синхронизация клиентских SQL-пакетов, тесты, ETL и QA H&N' `
    -Force | Out-Null

$reminderArguments = @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-WindowStyle', 'Hidden',
    '-File', ('"{0}"' -f $Reminder)
) -join ' '
$reminderAction = New-ScheduledTaskAction `
    -Execute $powerShellPath `
    -Argument $reminderArguments
$reminderTrigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday `
    -At $reminderTime

Register-ScheduledTask `
    -TaskName $ReminderTaskName `
    -Action $reminderAction `
    -Trigger $reminderTrigger `
    -Principal $principal `
    -Settings $settings `
    -Description 'Напоминание открыть RDP и выгрузить клиентский KPI до запуска ETL H&N' `
    -Force | Out-Null

Write-Host "Задача '$TaskName' установлена: каждый понедельник в $At."
Write-Host "Напоминание '$ReminderTaskName' установлено: каждый понедельник в $ReminderAt."
Write-Host 'Она запускается только в активной пользовательской сессии Windows.'
