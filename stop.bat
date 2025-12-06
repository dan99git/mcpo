@echo off
setlocal

set "PORTS=8000 8001"
echo Stopping services listening on ports: %PORTS%

powershell -NoProfile -Command ^
  "$ports = @(8000,8001);" ^
  "$killed = @();" ^
  "foreach ($port in $ports) {" ^
  "  $connections = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue;" ^
  "  foreach ($conn in $connections) {" ^
  "    $procId = $conn.OwningProcess;" ^
  "    if ($procId -and -not ($killed -contains $procId)) {" ^
  "      try {" ^
  "        Stop-Process -Id $procId -Force -ErrorAction Stop;" ^
  "        Write-Host ('Stopped PID {0} on port {1}' -f $procId, $port);" ^
  "        $killed += $procId;" ^
  "      } catch {" ^
  "        Write-Warning ('Failed to stop PID {0} on port {1}: {2}' -f $procId, $port, $_.Exception.Message);" ^
  "      }" ^
  "    }" ^
  "  }" ^
  "}" ^
  "if (-not $killed) { Write-Host 'No matching listeners found.' }"
if errorlevel 1 (
    echo Failed to stop one or more processes. Try running as Administrator.
) else (
    echo All requested ports have been processed.
)

endlocal
