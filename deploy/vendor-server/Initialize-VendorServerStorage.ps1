<#
.SYNOPSIS
  Initialize the Building OS Vendor Server directory structure on drive S.

.DESCRIPTION
  Creates directories only. No secret values, secret files, credentials,
  environment files, OAuth tokens or site access keys are generated.
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
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

    $current = $normalized
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
            if ($item.PSDrive.Name -ne 'S') {
                throw "Vendor Server storage path resolved outside drive S: $current"
            }
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Vendor Server storage path cannot use a reparse point: $current"
            }
        }

        $parent = [System.IO.Directory]::GetParent($current)
        if ($null -eq $parent) {
            break
        }
        $next = $parent.FullName.TrimEnd('\')
        if ([string]::Equals(
            $next,
            $current,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            break
        }
        $current = $next
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

$created = [System.Collections.Generic.List[string]]::new()
$existing = [System.Collections.Generic.List[string]]::new()
$planned = [System.Collections.Generic.List[string]]::new()

foreach ($relativeDirectory in $requiredRelativeDirectories) {
    $path = if ($relativeDirectory) {
        Join-Path -Path $validatedRoot -ChildPath $relativeDirectory
    } else {
        $validatedRoot
    }

    if (Test-Path -LiteralPath $path) {
        $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
        if (-not $item.PSIsContainer) {
            throw "Required Vendor Server path is not a directory: $path"
        }
        if ($item.PSDrive.Name -ne 'S') {
            throw "Required Vendor Server path resolved outside drive S: $path"
        }
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Required Vendor Server path cannot be a reparse point: $path"
        }
        $existing.Add($path)
        continue
    }

    if ($PSCmdlet.ShouldProcess($path, 'Create Vendor Server directory')) {
        $item = New-Item -ItemType Directory -Path $path -ErrorAction Stop
        if (-not $item.PSIsContainer -or $item.PSDrive.Name -ne 'S') {
            throw "Created Vendor Server path failed validation: $path"
        }
        $created.Add($path)
    } else {
        $planned.Add($path)
    }
}

[PSCustomObject]@{
    Root = $validatedRoot
    SiteId = $validatedSiteId
    Created = @($created)
    Existing = @($existing)
    Planned = @($planned)
}
