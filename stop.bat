@echo off
setlocal

rem Stop the watchdog FIRST so it cannot resurrect services mid-stop.
rem The watchdog writes its PID to logs\watchdog.pid; the IMAGENAME filter
rem guarantees a stale/reused PID never kills anything but a powershell.
set "WD_PID_FILE=%~dp0logs\watchdog.pid"
if not exist "%WD_PID_FILE%" goto WATCHDOG_DONE
set /p WDPID=<"%WD_PID_FILE%"
if not defined WDPID goto WATCHDOG_DONE
echo Stopping MCPO Watchdog (PID %WDPID%)...
taskkill /FI "PID eq %WDPID%" /FI "IMAGENAME eq powershell.exe" /T /F >nul 2>&1
del "%WD_PID_FILE%" >nul 2>&1
:WATCHDOG_DONE

set "PORTS=8000 8001 8351"
echo Stopping services listening on ports: %PORTS% (including their child process trees)

powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%~dp0tools\clear_ports.ps1' -Ports 8000,8001,8351"
if errorlevel 1 (
    echo Failed to stop one or more processes. Try running as Administrator.
) else (
    echo All requested ports have been processed.
)

endlocal
