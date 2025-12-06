@echo on
setlocal

rem Change to this script's directory
cd /d "%~dp0"
set "ROOT=%cd%"
set "VENV_BIN=%ROOT%\.venv\Scripts"
set "VENV_CFG=%ROOT%\.venv\pyvenv.cfg"

rem Ensure virtual environment exists; create if missing or broken (no pyvenv.cfg)
set "NEED_VENV=0"
if not exist "%VENV_BIN%\python.exe" set "NEED_VENV=1"
if not exist "%VENV_CFG%" set "NEED_VENV=1"

if "%NEED_VENV%"=="1" goto INIT_VENV
goto AFTER_VENV

:INIT_VENV
echo Preparing Python virtual environment in .venv...
if exist "%ROOT%\.venv" echo Detected missing/invalid pyvenv.cfg or incomplete venv. Cleaning .venv...
if exist "%ROOT%\.venv" rmdir /s /q "%ROOT%\.venv" 2>nul
set "PY_BOOT=py"
rem Detect Python launcher; fall back to python if not present
py -V >nul 2>&1
if errorlevel 1 set "PY_BOOT=python"
rem Prefer Python 3.11 if available via py launcher; fall back to default interpreter
"%PY_BOOT%" -3.11 -m venv "%ROOT%\.venv" 2>nul || "%PY_BOOT%" -m venv "%ROOT%\.venv"
if exist "%VENV_BIN%\python.exe" goto CHECK_VENV_CFG
echo Failed to create virtual environment. Ensure Python 3.11+ is installed and on PATH.
pause
exit /b 1

:CHECK_VENV_CFG
if exist "%VENV_CFG%" goto INSTALL_VENV_DEPS
echo Virtual environment appears invalid (missing pyvenv.cfg). Aborting.
pause
exit /b 1

:INSTALL_VENV_DEPS
echo Upgrading pip and installing dependencies...
"%VENV_BIN%\python.exe" -m pip install --upgrade pip
if exist "%ROOT%\requirements.txt" echo Installing dependencies from requirements.txt...
if exist "%ROOT%\requirements.txt" "%VENV_BIN%\python.exe" -m pip install -r "%ROOT%\requirements.txt"
if exist "%ROOT%\audio\whisper-server\WIN\requirements.txt" echo Installing Windows Whisper server dependencies...
if exist "%ROOT%\audio\whisper-server\WIN\requirements.txt" "%VENV_BIN%\python.exe" -m pip install -r "%ROOT%\audio\whisper-server\WIN\requirements.txt"
goto AFTER_VENV

:AFTER_VENV

rem Choose Python (prefer local venv)
set "PY_EXE=python"
if exist "%VENV_BIN%\python.exe" set "PY_EXE=%VENV_BIN%\python.exe"

rem Activate venv for this launcher session (optional but convenient)
if exist "%VENV_BIN%\activate.bat" call "%VENV_BIN%\activate.bat"

rem Optional install: run with --install to (re)install deps
if /i "%~1"=="--install" (
	if exist "%ROOT%\requirements.txt" (
		echo Installing dependencies from requirements.txt...
		"%PY_EXE%" -m pip install -r "%ROOT%\requirements.txt"
	)
	if exist "%ROOT%\audio\whisper-server\WIN\requirements.txt" (
		echo Installing Windows Whisper server dependencies...
		"%PY_EXE%" -m pip install -r "%ROOT%\audio\whisper-server\WIN\requirements.txt"
	)
)

set "LOG_DIR=%ROOT%\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
set "OPENAPI_LOG=%LOG_DIR%\openapi.log"
set "PROXY_LOG=%LOG_DIR%\proxy.log"
echo [Start %DATE% %TIME%] > "%OPENAPI_LOG%"
echo [Start %DATE% %TIME%] > "%PROXY_LOG%"

rem If MCPO_API_KEY is set, enforce auth on port 8000 (admin + completions)
set "AUTH_FLAGS="
if defined MCPO_API_KEY set "AUTH_FLAGS=--api-key %MCPO_API_KEY% --strict-auth"

rem Use module runners to avoid locking Windows console scripts during pip installs
rem (inline commands to avoid quote-escaping issues)

rem Whisper server moved to dev/audio/ (experimental - not included in stable release)
rem To enable: Uncomment and update path below
rem start "Whisper WIN 8002" cmd /k "cd /d %ROOT%\dev\audio\whisper-server\WIN & set AUTH_TOKEN=top-secret & set PORT=8002 & %PY_EXE% api_server.py"

rem Start MCPO Admin (FastAPI) on port 8000 in a new console window
start "MCPO Admin 8000" cmd /k "cd /d %ROOT% & set PYTHONPATH=%ROOT%\src & %PY_EXE% -m mcpo serve --config %ROOT%\mcpo.json --host 0.0.0.0 --port 8000 --hot-reload --env-path %ROOT%\.env --log-level debug %AUTH_FLAGS%"

rem Start MCPP Proxy (Streamable HTTP) on port 8001 in a new console window  
start "MCPP Proxy 8001" cmd /k "cd /d %ROOT% & set PYTHONPATH=%ROOT%\src & %PY_EXE% -m mcpo.proxy --config %ROOT%\mcpo.json --host 0.0.0.0 --port 8001 --stateless-http --env-path %ROOT%\.env --log-level debug"

echo.
echo ====================================================================
echo OpenHubUI Started Successfully!
echo ====================================================================
echo.
echo Admin UI:       http://localhost:8000/ui
echo API Docs:       http://localhost:8000/docs
echo.
echo Streamable HTTP: http://localhost:8001/{server-name}
echo   (For OpenWebUI integration)
echo.
echo Use stop.bat to terminate services cleanly.
echo ====================================================================
echo Close this window or press any key to exit launcher...
pause >nul
endlocal
