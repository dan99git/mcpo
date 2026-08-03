[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$WindowsIsoPath,

    [string]$VBoxManagePath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$VmName = "BOS-Client-Gateway"
$VmRoot = "D:\BOS-Client-Gateway-VM"
$VmDirectory = Join-Path $VmRoot $VmName
$DiskPath = Join-Path $VmDirectory "$VmName.vdi"
$MinimumVBoxVersion = [Version]"7.2.0"

function Resolve-VBoxManage([string]$ConfiguredPath) {
    if (-not [string]::IsNullOrWhiteSpace($ConfiguredPath)) {
        $resolved = [System.IO.Path]::GetFullPath($ConfiguredPath)
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            throw "VBoxManage was not found at the supplied path"
        }
        return $resolved
    }

    $command = Get-Command VBoxManage.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }

    $defaultPath = "C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
    if (Test-Path -LiteralPath $defaultPath -PathType Leaf) {
        return $defaultPath
    }
    throw "Oracle VirtualBox is not installed or VBoxManage.exe is not available"
}

$script:ResolvedVBoxManage = Resolve-VBoxManage $VBoxManagePath

function Invoke-VBoxManage([string[]]$Arguments) {
    $output = @(& $script:ResolvedVBoxManage @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        $detail = (@($output | ForEach-Object { $_.ToString() }) -join "`n").Trim()
        throw "VBoxManage command failed with exit code $exitCode`: $detail"
    }
    return @($output | ForEach-Object { $_.ToString() })
}

function ConvertFrom-VBoxMachineReadable([string[]]$Lines) {
    $values = @{}
    foreach ($line in $Lines) {
        if ($line -notmatch '^(?<key>[^=]+)=(?<value>.*)$') { continue }
        $key = $Matches.key.Trim().Trim('"')
        $value = $Matches.value.Trim()
        if ($value.Length -ge 2 -and $value[0] -eq '"' -and $value[-1] -eq '"') {
            $value = $value.Substring(1, $value.Length - 2)
            $value = $value.Replace('\"', '"').Replace('\\', '\')
        }
        $values[$key] = $value
    }
    return $values
}

$failures = [System.Collections.Generic.List[string]]::new()

function Add-Failure([string]$Message) {
    $script:failures.Add($Message)
}

function Assert-Value(
    [hashtable]$Values,
    [string]$Key,
    [string]$Expected
) {
    if (-not $Values.ContainsKey($Key)) {
        Add-Failure "missing machine-readable field $Key"
        return
    }
    if (-not [string]::Equals(
        [string]$Values[$Key],
        $Expected,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        Add-Failure "$Key expected $Expected but was $($Values[$Key])"
    }
}

$versionText = ((Invoke-VBoxManage @("--version")) -join "").Trim()
if ($versionText -notmatch '^(?<version>\d+\.\d+\.\d+)') {
    throw "VBoxManage returned an unrecognized version"
}
$actualVersion = [Version]$Matches.version
if ($actualVersion -lt $MinimumVBoxVersion) {
    throw "Oracle VirtualBox 7.2 or newer is required"
}
$showHelp = (Invoke-VBoxManage @("help", "showvminfo")) -join "`n"
if ($showHelp -notmatch [regex]::Escape("--machinereadable")) {
    throw "VBoxManage showvminfo does not advertise machine-readable output"
}

if (-not [System.IO.Path]::IsPathRooted($WindowsIsoPath)) {
    throw "WindowsIsoPath must be an absolute path"
}
$WindowsIsoPath = [System.IO.Path]::GetFullPath($WindowsIsoPath)
if (-not (Test-Path -LiteralPath $WindowsIsoPath -PathType Leaf)) {
    throw "expected Windows 11 ISO is missing"
}
if (-not (Test-Path -LiteralPath $DiskPath -PathType Leaf)) {
    throw "expected 80 GiB VDI is missing"
}

$machineLines = Invoke-VBoxManage @("showvminfo", $VmName, "--machinereadable")
$values = ConvertFrom-VBoxMachineReadable $machineLines

Assert-Value $values "VMState" "poweroff"
Assert-Value $values "memory" "8192"
Assert-Value $values "cpus" "4"
Assert-Value $values "firmware" "EFI"
Assert-Value $values "tpm-type" "2.0"
Assert-Value $values "nic1" "nat"
Assert-Value $values "nic2" "none"
Assert-Value $values "nic3" "none"
Assert-Value $values "nic4" "none"
Assert-Value $values "nic5" "none"
Assert-Value $values "nic6" "none"
Assert-Value $values "nic7" "none"
Assert-Value $values "nic8" "none"
Assert-Value $values "cableconnected1" "on"
Assert-Value $values "nictrace1" "off"
Assert-Value $values "nicpromisc1" "deny"
Assert-Value $values "natlocalhostreachable1" "off"
Assert-Value $values "clipboard-mode" "disabled"
Assert-Value $values "clipboard-file-transfers" "disabled"
Assert-Value $values "draganddrop" "disabled"
Assert-Value $values "usb" "off"
Assert-Value $values "ehci" "off"
Assert-Value $values "xhci" "off"
Assert-Value $values "audioin" "off"
Assert-Value $values "audioout" "off"
Assert-Value $values "vrde" "off"

if (-not $values.ContainsKey("ostype") -or $values.ostype -notmatch '^Windows 11 \(64-bit\)$') {
    Add-Failure "guest OS type is not Windows 11 (64-bit)"
}

if (-not $values.ContainsKey("CfgFile")) {
    Add-Failure "machine-readable VM settings path is missing"
}
else {
    $settingsPath = [System.IO.Path]::GetFullPath([string]$values.CfgFile)
    if (-not $settingsPath.StartsWith(
        "$VmDirectory\",
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        Add-Failure "VM settings are outside D:\BOS-Client-Gateway-VM"
    }
}

foreach ($entry in $values.GetEnumerator()) {
    if ($entry.Key -match '^nic(?<number>\d+)$') {
        $number = [int]$Matches.number
        if ($number -gt 1 -and $entry.Value -ne "none") {
            Add-Failure "extra NIC $number is enabled"
        }
    }
    if (
        $entry.Key -match '^(Forwarding\(\d+\)|natpf\d+)$' -and
        -not [string]::IsNullOrWhiteSpace([string]$entry.Value)
    ) {
        Add-Failure "NAT port forwarding is configured"
    }
    if ($entry.Key -match '^SharedFolder') {
        Add-Failure "a shared folder is configured"
    }
    if ($entry.Key -match '^USBFilter' -and $entry.Value -notmatch '^(off|disabled|none)?$') {
        Add-Failure "a USB filter is active"
    }
}

$attachedFiles = @(
    $values.GetEnumerator() |
        Where-Object { $_.Key -match '-\d+-\d+$' } |
        ForEach-Object { ([string]$_.Value).Replace('/', '\') }
)
$expectedDisk = $DiskPath.Replace('/', '\')
$expectedIso = $WindowsIsoPath.Replace('/', '\')
$attachedDisks = @($attachedFiles | Where-Object { $_ -match '\.(vdi|vmdk|vhd)$' })
$attachedIsos = @($attachedFiles | Where-Object { $_ -match '\.iso$' })
if (
    $attachedDisks.Count -ne 1 -or
    -not [string]::Equals(
        $attachedDisks[0],
        $expectedDisk,
        [System.StringComparison]::OrdinalIgnoreCase
    )
) {
    Add-Failure "the expected VDI is not the only attached virtual disk"
}
if (
    $attachedIsos.Count -ne 1 -or
    -not [string]::Equals(
        $attachedIsos[0],
        $expectedIso,
        [System.StringComparison]::OrdinalIgnoreCase
    )
) {
    Add-Failure "the expected Windows ISO is not the only attached ISO"
}

$mediumInfo = (Invoke-VBoxManage @("showmediuminfo", "disk", $DiskPath)) -join "`n"
if ($mediumInfo -notmatch '(?im)^Format:\s+VDI\s*$') {
    Add-Failure "virtual disk format is not VDI"
}
if ($mediumInfo -notmatch '(?im)^Capacity:\s+81920\s+MBytes\s*$') {
    Add-Failure "virtual disk capacity is not 80 GiB"
}
if ($mediumInfo -notmatch '(?im)^Variant:\s+dynamic\b') {
    Add-Failure "virtual disk is not dynamically allocated"
}

if ($failures.Count -gt 0) {
    throw "client gateway VM validation failed: $($failures -join '; ')"
}

[ordered]@{
    v = 1
    type = "client-gateway-vm.validation"
    state = "passed"
    vmName = $VmName
    vmRoot = $VmRoot
    diskPath = $DiskPath
    isoPath = $WindowsIsoPath
    virtualBoxVersion = $actualVersion.ToString()
} | ConvertTo-Json -Compress
