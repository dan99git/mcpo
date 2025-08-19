@echo off
echo Stopping MCPO Server...
echo.
echo Finding Python processes on port 8000...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000') do (
    echo Killing process ID: %%a
    taskkill /f /pid %%a >nul 2>&1
)
echo.
echo MCPO Server stopped.
pause
