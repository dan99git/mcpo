<#
.SYNOPSIS
  Crash-recovery watchdog for the mcpo stack. Restarts ONLY a crashed service.

.DESCRIPTION
  Probes the three services start.bat launches:
    - 8000 MCPO Admin  : GET http://localhost:8000/healthz, expect HTTP 200
                         (sends Bearer MCPO_API_KEY when set; harmless now that
                         port 8000 runs without --strict-auth, kept so the probe
                         still works if strict auth is ever restored)
    - 8001 MCPP Proxy  : TCP listen check (no health route on the proxy)
    - 8351 MCPO OAuth  : TCP listen check

  On a dead service: logs to logs\watchdog.log, clears ONLY that port via
  tools\clear_ports.ps1, relaunches ONLY that service with the exact command
  line start.bat uses (same PYTHONPATH, venv python, auth flags, new console).

  Restart-rate cap: max $MaxRestarts restarts per service per
  $RestartWindowSeconds. Exceeded -> logs ERROR and stops retrying that
  service until the watchdog itself is restarted. No infinite restart loops.

  An HTTP service that responds with a non-200 status is ALIVE (it is up and
  answering); that is logged as WARN, never restarted. Only a connection
  failure/timeout counts as dead.

.PARAMETER DryRun
  Log what would be done (port clear + relaunch command) without acting.

.PARAMETER IntervalSeconds
  Seconds between probe cycles. Default 15.

.PARAMETER RestartGraceSeconds
  After a restart, skip probing that service for this long so a slow startup
  is not counted as another crash. Default 45.

.PARAMETER StartupGraceSeconds
  After the watchdog starts, a service that has never yet been seen alive is
  NOT restarted for this long. start.bat launches the watchdog in the same
  breath as the services; without this the first probe fires seconds before
  the listeners bind and spawns duplicate consoles (race observed 2026-08-01
  at 21:20:56 and 21:26:02, one duplicate admin shell each time). Default 90.

.PARAMETER MaxCycles
  Stop after N probe cycles (testing aid). 0 = run forever. Default 0.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\watchdog.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\watchdog.ps1 -DryRun -MaxCycles 2
#>
param(
    [switch]$DryRun,
    [int]$IntervalSeconds = 15,
    [int]$RestartGraceSeconds = 45,
    [int]$StartupGraceSeconds = 90,
    [int]$MaxRestarts = 3,
    [int]$RestartWindowSeconds = 300,
    [int]$MaxCycles = 0
)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $Root 'logs'
$LogFile = Join-Path $LogDir 'watchdog.log'
$PidFile = Join-Path $LogDir 'watchdog.pid'
$ClearPortsScript = Join-Path $Root 'tools\clear_ports.ps1'

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

try { $Host.UI.RawUI.WindowTitle = 'MCPO Watchdog' } catch { }

function Write-Log {
    param(
        [Parameter(Mandatory = $true)][string]$Level,
        [Parameter(Mandatory = $true)][string]$Message
    )
    $line = '[{0}] [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    Add-Content -Path $LogFile -Value $line
    Write-Host $line
}

# --- Mirror start.bat's launch environment exactly -------------------------
$PyExe = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $PyExe)) { $PyExe = 'python' }

$ApiKey = $env:MCPO_API_KEY
$AuthFlags = ''        # start.bat AUTH_FLAGS (port 8000)
$ProxyAuthFlags = ''   # start.bat PROXY_AUTH_FLAGS (port 8001)
if ($ApiKey) {
    # No --strict-auth on 8000: it has no path exemptions and blocks the /ui
    # static mount, making the Admin UI unreachable from a browser. Port 8000
    # binds 127.0.0.1 instead. Must stay in step with start.bat AUTH_FLAGS.
    $AuthFlags = "--api-key $ApiKey"
    $ProxyAuthFlags = "--api-key $ApiKey"
}

# Command strings replicate start.bat lines 87/92/97 byte-for-byte (including
# the 'set PYTHONPATH=...\src ' construction). Title is applied via 'title X &'
# because 'start "title"' is a cmd-internal we do not go through here.
$Services = @(
    @{
        Name    = 'MCPO Admin 8000'
        Port    = 8000
        Check   = 'http'
        Url     = 'http://localhost:8000/healthz'
        Command = "cd /d $Root & set PYTHONPATH=$Root\src & $PyExe -m mcpo serve --config $Root\mcpo.json --host 127.0.0.1 --port 8000 --hot-reload --env-path $Root\.env --log-level debug $AuthFlags".TrimEnd()
    },
    @{
        Name    = 'MCPP Proxy 8001'
        Port    = 8001
        Check   = 'tcp'
        Command = "cd /d $Root & set PYTHONPATH=$Root\src & $PyExe -m mcpo.proxy --config $Root\mcpo.json --host 0.0.0.0 --port 8001 --hot-reload --env-path $Root\.env --log-level debug $ProxyAuthFlags".TrimEnd()
    },
    @{
        Name    = 'MCPO OAuth 8351'
        Port    = 8351
        Check   = 'tcp'
        Command = "cd /d $Root & set PYTHONPATH=$Root\src & $PyExe -m mcpo.proxy --config $Root\mcpo.json --host 127.0.0.1 --port 8351 --oauth --public-url https://dev.ai.lighting --hot-reload --env-path $Root\.env --log-level debug"
    }
)

# Per-service runtime state
$State = @{}
foreach ($svc in $Services) {
    $State[$svc.Name] = @{
        RestartTimes = New-Object System.Collections.Generic.List[datetime]
        LastRestart  = $null
        GivenUp      = $false
        LastStatus   = $null
        SeenUp       = $false
    }
}

function Test-ServiceStatus {
    # Returns 'healthy', 'responding' (HTTP up but not 200), or 'dead'.
    param([hashtable]$Svc)

    if ($Svc.Check -eq 'http') {
        $headers = @{}
        if ($ApiKey) { $headers['Authorization'] = "Bearer $ApiKey" }
        try {
            $resp = Invoke-WebRequest -Uri $Svc.Url -UseBasicParsing -TimeoutSec 5 -Headers $headers
            if ([int]$resp.StatusCode -eq 200) { return 'healthy' }
            return 'responding'
        } catch {
            if ($_.Exception.Response) {
                # Server answered with an error status: it is up, not crashed.
                return 'responding'
            }
            return 'dead'
        }
    }

    # TCP listen check (matches clear_ports.ps1's listener discovery).
    $listen = Get-NetTCPConnection -LocalPort $Svc.Port -State Listen -ErrorAction SilentlyContinue
    if ($listen) { return 'healthy' }
    return 'dead'
}

function Invoke-ServiceRestart {
    param([hashtable]$Svc)

    $st = $State[$Svc.Name]
    $now = Get-Date

    # Prune restart records older than the rate window, then enforce the cap.
    $recent = New-Object System.Collections.Generic.List[datetime]
    foreach ($t in $st.RestartTimes) {
        if (($now - $t).TotalSeconds -le $RestartWindowSeconds) { $recent.Add($t) }
    }
    $st.RestartTimes = $recent

    if ($st.RestartTimes.Count -ge $MaxRestarts) {
        $st.GivenUp = $true
        Write-Log 'ERROR' ("{0} crash-looping, giving up until watchdog restart ({1} restarts in the last {2}s)" -f $Svc.Name, $st.RestartTimes.Count, $RestartWindowSeconds)
        return
    }

    $st.RestartTimes.Add($now)
    $st.LastRestart = $now

    if ($DryRun) {
        # Redact the API key in the LOG only; the real relaunch uses the real key.
        $loggedCommand = $Svc.Command
        if ($ApiKey) { $loggedCommand = $loggedCommand.Replace("--api-key $ApiKey", '--api-key ***') }
        Write-Log 'DRYRUN' ("Would clear port {0} via clear_ports.ps1 -Ports {0}" -f $Svc.Port)
        Write-Log 'DRYRUN' ("Would relaunch {0} in a new console: cmd /k title {0} & {1}" -f $Svc.Name, $loggedCommand)
        return
    }

    Write-Log 'ACTION' ("Clearing port {0} (only this port)" -f $Svc.Port)
    & $ClearPortsScript -Ports @($Svc.Port) 2>&1 | ForEach-Object { Write-Log 'INFO' ("clear_ports: {0}" -f $_) }

    Write-Log 'ACTION' ("Relaunching {0} in a new console window" -f $Svc.Name)
    Start-Process -FilePath 'cmd.exe' -ArgumentList '/k', ("title {0} & {1}" -f $Svc.Name, $Svc.Command)
    Write-Log 'INFO' ("{0} relaunched (restart {1} of max {2} per {3}s); grace period {4}s" -f $Svc.Name, $st.RestartTimes.Count, $MaxRestarts, $RestartWindowSeconds, $RestartGraceSeconds)
}

# --- Startup ----------------------------------------------------------------
$WatchdogStart = Get-Date
Write-Log 'INFO' ("Watchdog starting. PID={0} DryRun={1} Interval={2}s Grace={3}s StartupGrace={4}s Cap={5}/{6}s Python={7} AuthFlags={8}" -f $PID, [bool]$DryRun, $IntervalSeconds, $RestartGraceSeconds, $StartupGraceSeconds, $MaxRestarts, $RestartWindowSeconds, $PyExe, $(if ($ApiKey) { 'enabled' } else { 'none' }))

if (-not $DryRun) {
    Set-Content -Path $PidFile -Value $PID
}

$cycle = 0
try {
    while ($true) {
        $cycle++
        foreach ($svc in $Services) {
            $st = $State[$svc.Name]

            if ($st.GivenUp) { continue }

            if ($st.LastRestart -and ((Get-Date) - $st.LastRestart).TotalSeconds -lt $RestartGraceSeconds) {
                Write-Log 'INFO' ("{0} in post-restart grace period, skipping probe" -f $svc.Name)
                continue
            }

            $status = Test-ServiceStatus -Svc $svc
            if ($status -ne 'dead') { $st.SeenUp = $true }

            if ($status -ne $st.LastStatus) {
                if ($status -eq 'healthy') {
                    Write-Log 'INFO' ("{0} is healthy ({1} check)" -f $svc.Name, $svc.Check)
                } elseif ($status -eq 'responding') {
                    Write-Log 'WARN' ("{0} responded non-200 on {1} - alive, NOT restarting" -f $svc.Name, $svc.Url)
                }
                $st.LastStatus = $status
            }

            if ($status -eq 'dead') {
                if (-not $st.SeenUp -and ((Get-Date) - $WatchdogStart).TotalSeconds -lt $StartupGraceSeconds) {
                    Write-Log 'INFO' ("{0} not up yet, inside startup grace ({1}s) - not restarting" -f $svc.Name, $StartupGraceSeconds)
                    continue
                }
                Write-Log 'ALERT' ("{0} is DOWN (port {1}, {2} check failed)" -f $svc.Name, $svc.Port, $svc.Check)
                Invoke-ServiceRestart -Svc $svc
            }
        }

        if ($MaxCycles -gt 0 -and $cycle -ge $MaxCycles) {
            Write-Log 'INFO' ("MaxCycles={0} reached, exiting" -f $MaxCycles)
            break
        }
        Start-Sleep -Seconds $IntervalSeconds
    }
} finally {
    if (-not $DryRun -and (Test-Path $PidFile)) {
        $stored = Get-Content -Path $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
        if ("$stored" -eq "$PID") {
            Remove-Item -Path $PidFile -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Log 'INFO' 'Watchdog stopped.'
}
