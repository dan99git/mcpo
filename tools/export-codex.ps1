param(
    [Parameter(Mandatory = $true)]
    [string]$Sid,
    [string]$OutFile
)

$ErrorActionPreference = 'Stop'

if (-not $OutFile -or [string]::IsNullOrWhiteSpace($OutFile)) {
    $OutFile = "codex-$Sid.md"
}

# Locate Codex sessions directory
$candidates = @(
    (Join-Path $HOME '.codex\sessions'),
    (Join-Path $HOME '.codex/sessions')
)
$root = $null
foreach ($c in $candidates) { if (Test-Path $c) { $root = $c; break } }
if (-not $root) { throw "Codex sessions directory not found. Checked: $($candidates -join ', ')" }

Write-Host "Searching logs under: $root"
$pattern = [regex]::Escape($Sid)
$log = Get-ChildItem -Path $root -Recurse -Filter '*.jsonl' |
    Where-Object { Select-String -Path $_.FullName -Pattern $pattern -Quiet } |
    Select-Object -First 1
if (-not $log) { throw "Session ID not found in any .jsonl under ${root}: $Sid" }
Write-Host ("Found log: " + $log.FullName)

# Output collector
$out = [System.Collections.Generic.List[string]]::new()
function AddL { param([string]$s) [void]$script:out.Add($s) }
function Fence { param([string]$lang, [string]$s) AddL('```' + $lang); AddL($s); AddL('```'); AddL("") }
function RenderContent { param($content)
    if ($null -eq $content) { return }
    foreach ($item in $content) {
        $type = $item.type
        if ($type -eq 'text') {
            AddL([string]$item.text)
            AddL("")
        } elseif ($type -eq 'input_text') {
            Fence 'text' ([string]$item.text)
        } elseif ($type -eq 'image') {
            AddL('- [image attachment]')
            AddL("")
        } else {
            Fence 'json' ((ConvertTo-Json $item -Depth 100))
        }
    }
}

# Process JSONL
Get-Content -LiteralPath $log.FullName | ForEach-Object {
    if ([string]::IsNullOrWhiteSpace($_)) { return }
    try { $obj = $_ | ConvertFrom-Json -ErrorAction Stop } catch { return }
    if ($null -eq $obj) { return }
    $hasType = $obj.PSObject.Properties.Name -contains 'type'
    if ($hasType -and $obj.type -eq 'message') {
        $role = ([string]$obj.role).ToUpper()
        $name = ''
        if ($obj.PSObject.Properties.Name -contains 'name' -and $obj.name) { $name = ' - ' + [string]$obj.name }
        AddL('### ' + $role + $name)
        AddL('')
        RenderContent $obj.content
    } elseif ($hasType) {
        AddL('### EVENT: ' + [string]$obj.type)
        AddL('')
        Fence 'json' ((ConvertTo-Json $obj -Depth 100))
    }
}

$outPath = Join-Path (Get-Location) $OutFile
$out | Set-Content -LiteralPath $outPath -Encoding UTF8
Write-Output "Wrote $outPath"
