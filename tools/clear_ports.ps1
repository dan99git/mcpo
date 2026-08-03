<#
.SYNOPSIS
  Kill whatever process is listening on each given TCP port, plus that
  process's full descendant tree (children, grandchildren, ...).

.DESCRIPTION
  mcpo's two launcher processes (port 8000 admin/REST, port 8001 MCP proxy)
  each spawn a fleet of MCP child processes (node/npx/pwsh/python...). Killing
  only the PID that owns the port orphans that fleet. This script walks
  Win32_Process.ParentProcessId to find the whole tree rooted at the port's
  listener, then kills the tree (taskkill /T handles the walk-and-kill; a
  short-lived straggler sweep catches anything that raced past it).

  Used by both stop.bat (tear down before exit) and start.bat (clear any old
  instance before a fresh launch, so a new launch replaces it instead of
  colliding with the port bind).

.PARAMETER Ports
  One or more TCP port numbers to clear.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\clear_ports.ps1 -Ports 8000,8001
#>
param(
    [Parameter(Mandatory = $true)]
    [int[]]$Ports
)

function Get-DescendantProcessIds {
    param([int]$RootId)

    # Snapshot all processes once; build a parent -> children map; BFS from RootId.
    $all = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Select-Object ProcessId, ParentProcessId
    $byParent = @{}
    foreach ($p in $all) {
        $procId = [int]$p.ProcessId
        $parentId = [int]$p.ParentProcessId
        if (-not $byParent.ContainsKey($parentId)) {
            $byParent[$parentId] = New-Object System.Collections.Generic.List[int]
        }
        $byParent[$parentId].Add($procId)
    }

    $result = New-Object System.Collections.Generic.List[int]
    $queue = New-Object System.Collections.Generic.Queue[int]
    $queue.Enqueue($RootId)
    while ($queue.Count -gt 0) {
        $current = $queue.Dequeue()
        if ($byParent.ContainsKey($current)) {
            foreach ($child in $byParent[$current]) {
                $result.Add($child)
                $queue.Enqueue($child)
            }
        }
    }
    return $result
}

$anyKilled = $false

foreach ($port in $Ports) {
    $connections = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    $rootPids = @($connections | Select-Object -ExpandProperty OwningProcess -Unique)

    if (-not $rootPids -or $rootPids.Count -eq 0) {
        Write-Host "Port $port : no listener found."
        continue
    }

    foreach ($rootPid in $rootPids) {
        $descendants = Get-DescendantProcessIds -RootId $rootPid
        $treeIds = @($rootPid) + $descendants | Sort-Object -Unique
        Write-Host ("Port {0}: listener PID {1}, tree size {2} (PIDs: {3})" -f $port, $rootPid, $treeIds.Count, ($treeIds -join ','))

        # taskkill /T walks and kills the full descendant tree for us; /F forces it.
        $out = & taskkill /PID $rootPid /T /F 2>&1
        Write-Host ($out -join "`n")

        # Straggler sweep: anything from our snapshot that survived the taskkill
        # (race, or a process that detached before the tree-kill reached it).
        Start-Sleep -Milliseconds 300
        foreach ($tid in $treeIds) {
            $proc = Get-Process -Id $tid -ErrorAction SilentlyContinue
            if ($proc) {
                try {
                    Stop-Process -Id $tid -Force -ErrorAction Stop
                    Write-Host ("  Force-stopped straggler PID {0}" -f $tid)
                } catch {
                    Write-Warning ("  Failed to stop straggler PID {0}: {1}" -f $tid, $_.Exception.Message)
                }
            }
        }

        $anyKilled = $true
    }
}

if (-not $anyKilled) {
    Write-Host "No matching listeners found on any of: $($Ports -join ', ')"
}
