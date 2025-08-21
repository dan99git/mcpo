@echo off
echo Testing MCPO Server Setup...
echo.
echo 1. Checking if mcpo.json exists...
if exist mcpo.json (
    echo   ✓ mcpo.json found
) else (
    echo   ✗ mcpo.json not found - create it first
    goto :end
)

echo.
echo 2. Checking if Python is available...
python --version >nul 2>&1
if %errorlevel% equ 0 (
    echo   ✓ Python is available
) else (
    echo   ✗ Python not found - install Python first
    goto :end
)

echo.
echo 3. Checking if mcpo module is installed...
python -c "import mcpo" >nul 2>&1
if %errorlevel% equ 0 (
    echo   ✓ mcpo module is available
) else (
    echo   ✗ mcpo module not found - install it first
    echo   Run: pip install -e .
    goto :end
)

echo.
echo 4. Checking port 8000 availability...
netstat -an | findstr :8000 >nul 2>&1
if %errorlevel% equ 0 (
    echo   ✗ Port 8000 is already in use
    echo   Run stop.bat first or use a different port
) else (
    echo   ✓ Port 8000 is available
)

echo.
echo Setup check complete!
echo.
echo To start the server, run: start.bat
echo To stop the server, run: stop.bat
echo.

:end
pause
