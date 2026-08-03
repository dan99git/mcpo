@echo off
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
if exist "%VENV_CFG%" goto SYNC_NEW_VENV
echo Virtual environment appears invalid (missing pyvenv.cfg). Aborting.
pause
exit /b 1

:SYNC_NEW_VENV
call :SYNC_PROJECT
if errorlevel 1 exit /b 1
goto AFTER_VENV

:AFTER_VENV

rem Choose Python (prefer local venv)
set "PY_EXE=python"
if exist "%VENV_BIN%\python.exe" set "PY_EXE=%VENV_BIN%\python.exe"

rem Activate venv for this launcher session (optional but convenient)
if exist "%VENV_BIN%\activate.bat" call "%VENV_BIN%\activate.bat"

rem --install remains accepted for compatibility; every launch performs a locked sync.

set "LOG_DIR=%ROOT%\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
rem Log files are owned by the apps themselves (rotating file logging in
rem src/mcpo/services/file_logging.py): logs\openapi.log, logs\proxy-8001.log,
rem logs\proxy-8351.log. Do NOT truncate them here; they persist across restarts.

rem If MCPO_API_KEY is set, enforce auth on port 8000 (admin + completions)
set "AUTH_FLAGS="
if defined MCPO_API_KEY set "AUTH_FLAGS=--api-key %MCPO_API_KEY% --strict-auth"

rem If MCPO_API_KEY is set, enforce auth on port 8001 (MCP streamable HTTP proxy)
set "PROXY_AUTH_FLAGS="
if defined MCPO_API_KEY set "PROXY_AUTH_FLAGS=--api-key %MCPO_API_KEY%"

rem Clear ports 8000/8001 of any previous instance (and its full MCP child process
rem tree) before launching, so this launch replaces the old one instead of colliding
rem with it on bind.
echo Clearing ports 8000 8001 8351 of any previous instance...
powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%ROOT%\tools\clear_ports.ps1' -Ports 8000,8001,8351"

call :SYNC_PROJECT
if errorlevel 1 exit /b 1

rem Use module runners to avoid locking Windows console scripts during dependency syncs
rem (inline commands to avoid quote-escaping issues)

rem Whisper server moved to dev/audio/ (experimental - not included in stable release)
rem To enable: Uncomment and update path below
rem start "Whisper WIN 8002" cmd /k "cd /d %ROOT%\dev\audio\whisper-server\WIN & set AUTH_TOKEN=top-secret & set PORT=8002 & %PY_EXE% api_server.py"

rem Start MCPO Admin (FastAPI) on port 8000 in a new console window
start "MCPO Admin 8000" cmd /k "cd /d %ROOT% & set PYTHONPATH=%ROOT%\src & %PY_EXE% -m mcpo serve --config %ROOT%\mcpo.json --host 0.0.0.0 --port 8000 --hot-reload --env-path %ROOT%\.env --log-level debug %AUTH_FLAGS%"

rem Start MCPP Proxy (Streamable HTTP) on port 8001 in a new console window
rem --hot-reload watches structural mcpo.json changes. Shared state toggles are
rem enforced per request and do not rebuild or kill MCP server runtimes.
start "MCPP Proxy 8001" cmd /k "cd /d %ROOT% & set PYTHONPATH=%ROOT%\src & %PY_EXE% -m mcpo.proxy --config %ROOT%\mcpo.json --host 0.0.0.0 --port 8001 --hot-reload --env-path %ROOT%\.env --log-level debug %PROXY_AUTH_FLAGS%"

rem Start MCPO OAuth Proxy (Streamable HTTP + self-hosted OAuth for ChatGPT / Claude
rem Desktop remote connectors) on port 8351. It shares 8001's MCP routes, tools,
rem filtering, calls, and toggle behavior. OAuth is the only added behavior.
start "MCPO OAuth 8351" cmd /k "cd /d %ROOT% & set PYTHONPATH=%ROOT%\src & %PY_EXE% -m mcpo.proxy --config %ROOT%\mcpo.json --host 127.0.0.1 --port 8351 --oauth --public-url https://dev.ai.lighting --hot-reload --env-path %ROOT%\.env --log-level debug"

rem Start crash-recovery watchdog in its own console window. It probes 8000
rem (/healthz), 8001 and 8351 (TCP listen) every 15s and restarts ONLY a
rem crashed service (max 3 restarts per service per 5 minutes). Logs to
rem logs\watchdog.log, writes its PID to logs\watchdog.pid for stop.bat.
start "MCPO Watchdog" powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\tools\watchdog.ps1"

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
echo OAuth MCP (8351): http://localhost:8351/mcp  ^| tunnel: https://dev.ai.lighting/mcp
echo   (For ChatGPT / Claude Desktop remote connectors)
echo.
echo Use stop.bat to terminate services cleanly.
echo ====================================================================
echo Launcher complete.
endlocal
exit /b 0

:SYNC_PROJECT
echo Syncing project dependencies from uv.lock...
uv sync --frozen --group dev
if errorlevel 1 (
	echo Failed to sync project dependencies with uv.
	exit /b 1
)
"%VENV_BIN%\python.exe" -c "import mcpo, typer"
if errorlevel 1 (
	echo Project import validation failed for mcpo or typer.
	exit /b 1
)
exit /b 0
