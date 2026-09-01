$ErrorActionPreference = 'Stop'

if (-not ('HnWindowsCredential.NativeMethods' -as [type])) {
    Add-Type @'
using System;
using System.Runtime.InteropServices;

namespace HnWindowsCredential
{
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct NativeCredential
    {
        public UInt32 Flags;
        public UInt32 Type;
        public string TargetName;
        public string Comment;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public UInt32 CredentialBlobSize;
        public IntPtr CredentialBlob;
        public UInt32 Persist;
        public UInt32 AttributeCount;
        public IntPtr Attributes;
        public string TargetAlias;
        public string UserName;
    }

    public static class NativeMethods
    {
        [DllImport("advapi32.dll", EntryPoint = "CredWriteW", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern bool CredWrite(ref NativeCredential credential, UInt32 flags);

        [DllImport("advapi32.dll", EntryPoint = "CredReadW", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern bool CredRead(string target, UInt32 type, UInt32 flags, out IntPtr credentialPtr);

        [DllImport("advapi32.dll", EntryPoint = "CredDeleteW", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern bool CredDelete(string target, UInt32 type, UInt32 flags);

        [DllImport("advapi32.dll", SetLastError = false)]
        public static extern void CredFree(IntPtr credentialPtr);
    }
}
'@
}

$script:GenericCredentialType = 1
$script:LocalMachinePersistence = 2
$script:CredentialNotFound = 1168

function Set-HnWindowsCredential {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Target,
        [Parameter(Mandatory)]
        [PSCredential]$Credential
    )

    $password = $Credential.GetNetworkCredential().Password
    $passwordBytes = [System.Text.Encoding]::Unicode.GetBytes($password)
    $blobPointer = [Runtime.InteropServices.Marshal]::AllocCoTaskMem($passwordBytes.Length)
    try {
        [Runtime.InteropServices.Marshal]::Copy($passwordBytes, 0, $blobPointer, $passwordBytes.Length)
        $nativeCredential = [HnWindowsCredential.NativeCredential]::new()
        $nativeCredential.Type = $script:GenericCredentialType
        $nativeCredential.TargetName = $Target
        $nativeCredential.CredentialBlobSize = $passwordBytes.Length
        $nativeCredential.CredentialBlob = $blobPointer
        $nativeCredential.Persist = $script:LocalMachinePersistence
        $nativeCredential.UserName = $Credential.UserName

        if (-not [HnWindowsCredential.NativeMethods]::CredWrite([ref]$nativeCredential, 0)) {
            $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
            throw [ComponentModel.Win32Exception]::new($errorCode)
        }
    }
    finally {
        if ($blobPointer -ne [IntPtr]::Zero) {
            for ($index = 0; $index -lt $passwordBytes.Length; $index++) {
                [Runtime.InteropServices.Marshal]::WriteByte($blobPointer, $index, 0)
            }
            [Runtime.InteropServices.Marshal]::FreeCoTaskMem($blobPointer)
        }
        if ($passwordBytes.Length -gt 0) {
            [Array]::Clear($passwordBytes, 0, $passwordBytes.Length)
        }
        $password = $null
    }
}

function Get-HnWindowsCredential {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Target
    )

    $credentialPointer = [IntPtr]::Zero
    if (-not [HnWindowsCredential.NativeMethods]::CredRead(
        $Target,
        $script:GenericCredentialType,
        0,
        [ref]$credentialPointer
    )) {
        $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        if ($errorCode -eq $script:CredentialNotFound) {
            return $null
        }
        throw [ComponentModel.Win32Exception]::new($errorCode)
    }

    try {
        $nativeCredential = [Runtime.InteropServices.Marshal]::PtrToStructure(
            $credentialPointer,
            [type][HnWindowsCredential.NativeCredential]
        )
        $passwordBytes = [byte[]]::new($nativeCredential.CredentialBlobSize)
        if ($passwordBytes.Length -gt 0) {
            [Runtime.InteropServices.Marshal]::Copy(
                $nativeCredential.CredentialBlob,
                $passwordBytes,
                0,
                $passwordBytes.Length
            )
        }
        $password = [System.Text.Encoding]::Unicode.GetString($passwordBytes)
        $securePassword = ConvertTo-SecureString -String $password -AsPlainText -Force
        return [PSCredential]::new($nativeCredential.UserName, $securePassword)
    }
    finally {
        if ($passwordBytes -and $passwordBytes.Length -gt 0) {
            [Array]::Clear($passwordBytes, 0, $passwordBytes.Length)
        }
        $password = $null
        [HnWindowsCredential.NativeMethods]::CredFree($credentialPointer)
    }
}

function Remove-HnWindowsCredential {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Target
    )

    if (-not [HnWindowsCredential.NativeMethods]::CredDelete(
        $Target,
        $script:GenericCredentialType,
        0
    )) {
        $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        if ($errorCode -ne $script:CredentialNotFound) {
            throw [ComponentModel.Win32Exception]::new($errorCode)
        }
    }
}

Export-ModuleMember -Function `
    Set-HnWindowsCredential, `
    Get-HnWindowsCredential, `
    Remove-HnWindowsCredential
