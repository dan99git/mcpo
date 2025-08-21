#!/bin/bash
echo "Stopping MCPO Server..."
echo ""
echo "Finding Python processes on port 8000..."
PID=$(lsof -ti:8000)
if [ ! -z "$PID" ]; then
    echo "Killing process ID: $PID"
    kill -9 $PID
    echo "MCPO Server stopped."
else
    echo "No process found on port 8000."
fi
echo ""
