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
$OsType = "Windows11_64"
$MinimumVBoxVersion = [Version]"7.2.0"
$RequiredFreeBytes = 90GB

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

function Assert-HelpCapability(
    [string]$Command,
    [string[]]$RequiredTokens
) {
    $helpText = (Invoke-VBoxManage @("help", $Command)) -join "`n"
    foreach ($token in $RequiredTokens) {
        if ($helpText -notmatch [regex]::Escape($token)) {
            throw "VBoxManage $Command does not advertise required capability $token"
        }
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

$requiredCapabilities = [ordered]@{
    createvm = @(
        "--name",
        "--platform-architecture",
        "--basefolder",
        "--ostype",
        "--register"
    )
    modifyvm = @(
        "--memory",
        "--cpus",
        "--firmware",
        "--tpm-type",
        "--boot",
        "--nic",
        "--nat-localhostreachable",
        "--clipboard-mode",
        "--clipboard-file-transfers",
        "--drag-and-drop",
        "--usb-ohci",
        "--usb-ehci",
        "--usb-xhci",
        "--vrde",
        "--audio-enabled"
    )
    createmedium = @("--filename", "--size", "--format", "--variant")
    storagectl = @("--name", "--add", "--controller", "--portcount", "--bootable")
    storageattach = @("--storagectl", "--port", "--device", "--type", "--medium")
    showvminfo = @("--machinereadable")
}
foreach ($command in $requiredCapabilities.Keys) {
    $help = Invoke-VBoxManage @("help", $command)
    $helpText = $help -join "`n"
    foreach ($token in $requiredCapabilities[$command]) {
        if ($helpText -notmatch [regex]::Escape($token)) {
            throw "VBoxManage $command does not advertise required capability $token"
        }
    }
}

if (-not [System.IO.Path]::IsPathRooted($WindowsIsoPath)) {
    throw "WindowsIsoPath must be an absolute path"
}
$WindowsIsoPath = [System.IO.Path]::GetFullPath($WindowsIsoPath)
if (-not (Test-Path -LiteralPath $WindowsIsoPath -PathType Leaf)) {
    throw "Windows 11 ISO was not found"
}
if (-not [System.IO.Path]::GetExtension($WindowsIsoPath).Equals(
    ".iso",
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "WindowsIsoPath must identify an ISO file"
}
$iso = Get-Item -LiteralPath $WindowsIsoPath
if ($iso.Length -le 0) {
    throw "Windows 11 ISO is empty"
}
$isoStream = [System.IO.File]::Open(
    $WindowsIsoPath,
    [System.IO.FileMode]::Open,
    [System.IO.FileAccess]::Read,
    [System.IO.FileShare]::Read
)
$isoStream.Dispose()

if (Test-Path -LiteralPath $VmRoot -PathType Leaf) {
    throw "VM root exists as a file"
}
if (Test-Path -LiteralPath $VmDirectory) {
    throw "VM directory already exists; refusing to modify or overwrite it"
}
if (Test-Path -LiteralPath $DiskPath) {
    throw "VM disk already exists; refusing to modify or overwrite it"
}

$drive = Get-PSDrive -Name "D" -PSProvider FileSystem -ErrorAction Stop
if ([int64]$drive.Free -lt [int64]$RequiredFreeBytes) {
    throw "D: requires at least 90 GiB free for the 80 GiB VM disk and headroom"
}

$availableOsTypes = (Invoke-VBoxManage @(
    "list",
    "--platform-arch=x86",
    "ostypes"
)) -join "`n"
if ($availableOsTypes -notmatch "(?m)^ID:\s+$([regex]::Escape($OsType))\s*$") {
    throw "VBoxManage does not provide the required Windows11_64 OS type"
}

$registeredVms = (Invoke-VBoxManage @("list", "vms")) -join "`n"
if ($registeredVms -match "(?m)^`"$([regex]::Escape($VmName))`"\s+\{[0-9a-f-]+\}\s*$") {
    throw "A registered VM named BOS-Client-Gateway already exists"
}

$registeredDisks = (Invoke-VBoxManage @("list", "hdds")) -join "`n"
if ($registeredDisks -match [regex]::Escape($DiskPath)) {
    throw "The intended VM disk path is already registered"
}

if (-not (Test-Path -LiteralPath $VmRoot -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $VmRoot)
}

$phase = "createvm"
try {
    [void](Invoke-VBoxManage @(
        "createvm",
        "--name=$VmName",
        "--platform-architecture=x86",
        "--basefolder=$VmRoot",
        "--ostype=$OsType",
        "--register"
    ))

    $phase = "modifyvm"
    [void](Invoke-VBoxManage @(
        "modifyvm",
        $VmName,
        "--os-type=$OsType",
        "--memory=8192",
        "--cpus=4",
        "--firmware=efi",
        "--tpm-type=2.0",
        "--acpi=on",
        "--ioapic=on",
        "--x86-long-mode=on",
        "--hwvirtex=on",
        "--nested-paging=on",
        "--paravirt-provider=hyperv",
        "--graphicscontroller=vboxsvga",
        "--vram=128",
        "--accelerate-3d=off",
        "--boot", "1=dvd",
        "--boot", "2=disk",
        "--boot", "3=none",
        "--boot", "4=none",
        "--nic", "1=nat",
        "--cable-connected", "1=on",
        "--nic-promisc", "1=deny",
        "--nic-trace", "1=off",
        "--nat-localhostreachable", "1=off",
        "--nic", "2=none",
        "--nic", "3=none",
        "--nic", "4=none",
        "--nic", "5=none",
        "--nic", "6=none",
        "--nic", "7=none",
        "--nic", "8=none",
        "--clipboard-mode=disabled",
        "--clipboard-file-transfers=disabled",
        "--drag-and-drop=disabled",
        "--usb-ohci=off",
        "--usb-ehci=off",
        "--usb-xhci=off",
        "--audio-enabled=off",
        "--audio-in=off",
        "--audio-out=off",
        "--vrde=off",
        "--uart", "1=off",
        "--uart", "2=off",
        "--lpt", "1=off"
    ))

    $phase = "createmedium"
    [void](Invoke-VBoxManage @(
        "createmedium",
        "disk",
        "--filename=$DiskPath",
        "--size=81920",
        "--format=VDI",
        "--variant=Standard"
    ))

    $phase = "storagectl-sata"
    [void](Invoke-VBoxManage @(
        "storagectl",
        $VmName,
        "--name=SATA Controller",
        "--add=sata",
        "--controller=IntelAhci",
        "--portcount=1",
        "--bootable=on"
    ))

    $phase = "storageattach-disk"
    [void](Invoke-VBoxManage @(
        "storageattach",
        $VmName,
        "--storagectl=SATA Controller",
        "--port=0",
        "--device=0",
        "--type=hdd",
        "--medium=$DiskPath"
    ))

    $phase = "storagectl-ide"
    [void](Invoke-VBoxManage @(
        "storagectl",
        $VmName,
        "--name=IDE Controller",
        "--add=ide",
        "--controller=PIIX4",
        "--portcount=2",
        "--bootable=on"
    ))

    $phase = "storageattach-iso"
    [void](Invoke-VBoxManage @(
        "storageattach",
        $VmName,
        "--storagectl=IDE Controller",
        "--port=0",
        "--device=0",
        "--type=dvddrive",
        "--medium=$WindowsIsoPath"
    ))
}
catch {
    throw "VM creation stopped during $phase. No cleanup or overwrite was attempted. Inspect the registered VM and D:\BOS-Client-Gateway-VM before any retry. $($_.Exception.Message)"
}

[ordered]@{
    v = 1
    type = "client-gateway-vm.create"
    state = "prepared"
    vmName = $VmName
    vmRoot = $VmRoot
    diskPath = $DiskPath
    isoPath = $WindowsIsoPath
    virtualBoxVersion = $actualVersion.ToString()
    started = $false
} | ConvertTo-Json -Compress
