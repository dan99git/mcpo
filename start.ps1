#!/usr/bin/env pwsh
# PowerShell script to start MCPO Server
# Compatible with both Windows PowerShell and PowerShell Core

Write-Host "Starting MCPO Server on port 8000..." -ForegroundColor Green
Write-Host ""
Write-Host "Using config file: mcpo.json" -ForegroundColor Cyan
Write-Host "Web UI will be available at: http://localhost:8000/mcp" -ForegroundColor Yellow
Write-Host ""

# Start the MCPO server with hot-reload enabled
python -m mcpo --host 0.0.0.0 --port 8000 --config mcpo.json --hot-reload