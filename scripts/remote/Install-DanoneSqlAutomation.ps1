[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'HN\DanoneSQL'),
    [string]$CredentialTarget = 'HN_Danone_SQL',
    [switch]$SkipCredentialSetup
)

$ErrorActionPreference = 'Stop'

New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
$files = @(
    'DanoneSqlCredential.psm1',
    'Manage-DanoneSqlCredential.ps1',
    'Export-DanoneKpiPrototype.ps1',
    'Export-DanoneKpiHistory.ps1'
)
foreach ($file in $files) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $file) -Destination (Join-Path $InstallRoot $file) -Force
}

$powerShellPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$desktop = [Environment]::GetFolderPath('Desktop')
$shell = New-Object -ComObject WScript.Shell

$exportShortcut = $shell.CreateShortcut((Join-Path $desktop 'Обновить KPI.lnk'))
$exportShortcut.TargetPath = $powerShellPath
$exportShortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$InstallRoot\Export-DanoneKpiPrototype.ps1`" -TrustServerCertificate -TransferRoot `"\\tsclient\H`""
$exportShortcut.WorkingDirectory = $InstallRoot
$exportShortcut.WindowStyle = 1
$exportShortcut.Save()

$credentialShortcut = $shell.CreateShortcut((Join-Path $desktop 'Обновить пароль SQL.lnk'))
$credentialShortcut.TargetPath = $powerShellPath
$credentialShortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$InstallRoot\Manage-DanoneSqlCredential.ps1`" -Mode Set -TrustServerCertificate"
$credentialShortcut.WorkingDirectory = $InstallRoot
$credentialShortcut.WindowStyle = 1
$credentialShortcut.Save()

if (-not $SkipCredentialSetup.IsPresent) {
    & (Join-Path $InstallRoot 'Manage-DanoneSqlCredential.ps1') `
        -Mode Set `
        -Target $CredentialTarget `
        -TrustServerCertificate
}

Write-Host "Установка завершена: $InstallRoot"
Write-Host 'Созданы ярлыки: Обновить KPI, Обновить пароль SQL'
