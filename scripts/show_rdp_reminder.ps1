[CmdletBinding()]
param(
    [int]$TimeoutSeconds = 600,
    [switch]$PreviewTextOnly
)

$ErrorActionPreference = 'Stop'
$Title = 'H&N Dashboard — выгрузить KPI через RDP'
$Message = @'
Сегодня в 14:00 автоматически запустится обновление H&N Dashboard.

До запуска ETL нужно:
1. Подключиться к удалённому рабочему столу клиента.
2. Открыть PowerShell.
3. Запустить на диске H: сценарий Run-DanoneKpiWeekly.ps1.
4. Дождаться сообщения, что ZIP передан на диск H:.

Если выгрузку пропустить, остальные источники обновятся, но KPI останется за последний доступный период.
'@

if ($PreviewTextOnly) {
    Write-Host $Title
    Write-Host $Message
    exit 0
}

$WshShell = New-Object -ComObject WScript.Shell
$null = $WshShell.Popup($Message, $TimeoutSeconds, $Title, 64)

