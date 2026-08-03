<#
.SYNOPSIS
  Validate the Building OS Vendor Server directory structure on drive S.

.DESCRIPTION
  Performs read-only checks of the required root and per-site directories.
#>
[CmdletBinding()]
param(
    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$Root = 'S:\BOS-Vendor',

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$SiteId = '44354'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-ValidatedSiteId {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Candidate
    )

    if (-not [System.Text.RegularExpressions.Regex]::IsMatch($Candidate, '^\d{5}$')) {
        throw "SiteId must be exactly five decimal digits in range 10000..65535: $Candidate"
    }

    $siteNumber = [int]::Parse(
        $Candidate,
        [System.Globalization.CultureInfo]::InvariantCulture
    )
    if ($siteNumber -lt 10000 -or $siteNumber -gt 65535) {
        throw "SiteId must be exactly five decimal digits in range 10000..65535: $Candidate"
    }

    return $Candidate
}

function Get-ValidatedVendorStorageRoot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Candidate
    )

    if ([string]::IsNullOrWhiteSpace($Candidate)) {
        throw 'Vendor Server storage root cannot be empty.'
    }
    if (-not [System.IO.Path]::IsPathFullyQualified($Candidate)) {
        throw "Vendor Server storage root must be fully qualified on drive S: $Candidate"
    }

    try {
        $fullPath = [System.IO.Path]::GetFullPath($Candidate)
    } catch {
        throw "Vendor Server storage root is invalid: $Candidate"
    }

    $pathRoot = [System.IO.Path]::GetPathRoot($fullPath)
    if (-not [string]::Equals(
        $pathRoot,
        'S:\',
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Vendor Server storage root must be on drive S: $Candidate"
    }

    $normalized = $fullPath.TrimEnd('\')
    if ([string]::Equals(
        $normalized,
        'S:',
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw 'Vendor Server storage root must be below S:\ and cannot be the drive root.'
    }

    $drive = Get-PSDrive -Name S -PSProvider FileSystem -ErrorAction Stop
    if ($drive.Provider.Name -ne 'FileSystem') {
        throw 'Drive S must use the FileSystem provider.'
    }

    return $normalized
}

$validatedSiteId = Get-ValidatedSiteId -Candidate $SiteId
$validatedRoot = Get-ValidatedVendorStorageRoot -Candidate $Root
$siteRoot = "sites\$validatedSiteId"
$requiredRelativeDirectories = @(
    ''
    'releases'
    'compose'
    'config'
    'state'
    'logs'
    'backups'
    'rollback'
    'sites'
    $siteRoot
    "$siteRoot\config"
    "$siteRoot\state"
    "$siteRoot\logs"
    "$siteRoot\backups"
    "$siteRoot\rollback"
    "$siteRoot\secrets"
)

$validatedDirectories = [System.Collections.Generic.List[string]]::new()
$problems = [System.Collections.Generic.List[string]]::new()

foreach ($relativeDirectory in $requiredRelativeDirectories) {
    $path = if ($relativeDirectory) {
        Join-Path -Path $validatedRoot -ChildPath $relativeDirectory
    } else {
        $validatedRoot
    }

    if (-not (Test-Path -LiteralPath $path)) {
        $problems.Add("Missing directory: $path")
        continue
    }

    $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
    if (-not $item.PSIsContainer) {
        $problems.Add("Path is not a directory: $path")
        continue
    }
    if ($item.PSDrive.Name -ne 'S') {
        $problems.Add("Path resolved outside drive S: $path")
        continue
    }
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        $problems.Add("Path is a reparse point: $path")
        continue
    }

    $validatedDirectories.Add($path)
}

if ($problems.Count -gt 0) {
    throw ($problems -join [Environment]::NewLine)
}

[PSCustomObject]@{
    Root = $validatedRoot
    SiteId = $validatedSiteId
    Status = 'Valid'
    Directories = @($validatedDirectories)
}
