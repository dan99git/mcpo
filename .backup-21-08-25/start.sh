#!/bin/bash
echo "Starting MCPO Server on port 8000..."
echo ""
echo "Using config file: mcpo.json"
echo "Web UI will be available at: http://localhost:8000/mcp"
echo ""
python -m mcpo --host 0.0.0.0 --port 8000 --config mcpo.json --hot-reload
