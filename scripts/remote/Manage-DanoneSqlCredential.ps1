[CmdletBinding()]
param(
    [ValidateSet('Set', 'Test', 'Remove')]
    [string]$Mode = 'Set',
    [string]$Target = 'HN_Danone_SQL',
    [string]$Server = 'danoneDB.dedicorp.ru,1433',
    [string]$Database = 'HnnDW',
    [switch]$TrustServerCertificate
)

$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'DanoneSqlCredential.psm1') -Force

function Test-DanoneSqlCredential {
    param([Parameter(Mandatory)][PSCredential]$Credential)

    $builder = [System.Data.SqlClient.SqlConnectionStringBuilder]::new()
    $builder['Data Source'] = $Server
    $builder['Initial Catalog'] = $Database
    $builder['User ID'] = $Credential.UserName
    $builder['Password'] = $Credential.GetNetworkCredential().Password
    $builder['Integrated Security'] = $false
    $builder['Connect Timeout'] = 15
    $builder['Encrypt'] = $true
    $builder['TrustServerCertificate'] = $TrustServerCertificate.IsPresent
    $builder['ApplicationIntent'] = 'ReadOnly'

    $connection = [System.Data.SqlClient.SqlConnection]::new($builder.ConnectionString)
    try {
        $connection.Open()
        $command = $connection.CreateCommand()
        $command.CommandText = 'SELECT DB_NAME() AS database_name, ORIGINAL_LOGIN() AS login_name;'
        $reader = $command.ExecuteReader()
        if (-not $reader.Read()) {
            throw 'SQL Server не вернул результат проверки.'
        }
        Write-Host "Подключение успешно: $($reader['database_name']) / $($reader['login_name'])"
        $reader.Dispose()
        $command.Dispose()
    }
    finally {
        $connection.Dispose()
    }
}

switch ($Mode) {
    'Set' {
        $current = Get-HnWindowsCredential -Target $Target
        $defaultUser = if ($current) { $current.UserName } else { 'opendw' }
        $enteredUser = Read-Host "Введите SQL-логин [$defaultUser]"
        $userName = if ([string]::IsNullOrWhiteSpace($enteredUser)) { $defaultUser } else { $enteredUser.Trim() }
        $securePassword = Read-Host 'Введите SQL-пароль' -AsSecureString
        if ($securePassword.Length -eq 0) {
            throw 'Пароль не указан.'
        }
        $credential = [PSCredential]::new($userName, $securePassword)
        Set-HnWindowsCredential -Target $Target -Credential $credential
        Test-DanoneSqlCredential -Credential $credential
        Write-Host "Учётные данные сохранены: $Target"
    }
    'Test' {
        $credential = Get-HnWindowsCredential -Target $Target
        if (-not $credential) {
            throw "Учётные данные $Target не найдены. Запустите режим Set."
        }
        Test-DanoneSqlCredential -Credential $credential
    }
    'Remove' {
        Remove-HnWindowsCredential -Target $Target
        Write-Host "Учётные данные удалены: $Target"
    }
}
