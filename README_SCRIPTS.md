# MCPO Testing Scripts

Quick start/stop scripts for testing MCPO on port 8000.

## Files Created

### Windows (`.bat` files)
- **`start.bat`** - Starts MCPO server on port 8000 with hot-reload
- **`stop.bat`** - Stops MCPO server by killing processes on port 8000
- **`test.bat`** - Checks if system is ready to run MCPO

### Linux/Mac (`.sh` files)  
- **`start.sh`** - Starts MCPO server on port 8000 with hot-reload
- **`stop.sh`** - Stops MCPO server by killing processes on port 8000

## Usage

### Windows
```cmd
# Check if everything is ready
test.bat

# Start the server
start.bat

# Stop the server (in another terminal)
stop.bat
```

### Linux/Mac
```bash
# Start the server
./start.sh

# Stop the server (in another terminal)  
./stop.sh
```

## What the Scripts Do

### Start Scripts
- Start MCPO on `0.0.0.0:8000` 
- Use `mcpo.json` config file
- Enable hot-reload for development
- Web UI available at: http://localhost:8000/mcp

### Stop Scripts
- Find processes using port 8000
- Forcefully terminate them
- Confirm server is stopped

### Test Script (Windows only)
- Checks if `mcpo.json` exists
- Verifies Python is installed
- Confirms mcpo module is available
- Tests if port 8000 is free

## Prerequisites

1. **Python** installed and in PATH
2. **mcpo module** installed (`pip install -e .`)
3. **mcpo.json** config file exists
4. **Port 8000** available

## Expected Output

When running `start.bat` or `start.sh`, you should see:
```
Starting MCPO Server on port 8000...

Using config file: mcpo.json
Web UI will be available at: http://localhost:8000/mcp

[Timestamp] - INFO - Starting MCPO Server...
[Timestamp] - INFO - Loading MCP server configurations from: mcpo.json
...
```

Access the UI at: **http://localhost:8000/mcp**

## Troubleshooting

- **Port already in use**: Run stop script first
- **mcpo module not found**: Run `pip install -e .`
- **Config file missing**: Ensure `mcpo.json` exists
- **Permission denied**: On Linux/Mac, ensure scripts are executable
