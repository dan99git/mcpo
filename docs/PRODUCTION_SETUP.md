# MCPO Production Setup Guide

This guide will help you set up MCPO in a Python virtual environment for production deployment.

## Quick Setup

### Automatic Setup (Recommended)

Run the setup script to automatically configure everything:

```bash
# Basic setup with defaults
python setup.py

# Custom setup
python setup.py --venv my_venv --config my_config.json --port 8080
```

### Manual Setup

If you prefer manual setup:

```bash
# 1. Create virtual environment
python -m venv venv

# 2. Activate virtual environment
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# 3. Install MCPO
pip install -e .

# 4. Install additional requirements (if any)
pip install -r requirements.txt

# 5. Copy configuration
cp mcpo.json.example mcpo.json
cp .env.example .env

# 6. Edit configuration and environment files
# Edit mcpo.json and .env as needed
```

## Configuration

### 1. Configuration File (`mcpo.json`)

Edit your configuration file to define MCP servers:

```json
{
  "mcpServers": {
    "perplexity": {
      "command": "npx",
      "args": ["-y", "perplexity-mcp"],
      "enabled": true,
      "env": {
        "PERPLEXITY_API_KEY": "your-api-key-here"
      }
    },
    "time": {
      "command": "uvx",
      "args": ["mcp-server-time"],
      "enabled": true
    }
  }
}
```

### 2. Environment Variables (`.env`)

Store sensitive API keys in the `.env` file:

```bash
MCPO_API_KEY=your-secure-api-key
PERPLEXITY_API_KEY=your-perplexity-key
OPENAI_API_KEY=your-openai-key
```

**Security Note:** The `.env` file is automatically added to `.gitignore` to prevent accidental commits of sensitive data.

## Running in Production

### Start the Server

**Windows:**
```cmd
start_production.bat
```

**Linux/Mac:**
```bash
./start_production.sh
```

### Stop the Server

**Windows:**
```cmd
stop_production.bat
```

**Linux/Mac:**
```bash
./stop_production.sh
```

## Accessing MCPO

Once running, access MCPO at:

- **Web UI:** http://localhost:8000/mcp
- **Main OpenAPI Docs:** http://localhost:8000/docs
- **Management API:** http://localhost:8000/openapi.json
- **Internal MCP Tools:** http://localhost:8000/mcpo/openapi.json

## Management Features

MCPO includes built-in management tools accessible via the internal MCP server at `/mcpo`:

### Available Tools

1. **`install_python_package`** - Install Python packages via pip
2. **`get_config`** - Retrieve current configuration
3. **`post_config`** - Update configuration and reload servers
4. **`get_logs`** - Get recent server logs
5. **`post_env`** - Securely update environment variables

### Example Usage

```bash
# Get configuration
curl -X POST http://localhost:8000/mcpo/get_config -H "Content-Type: application/json" -d "{}"

# Update environment variables
curl -X POST http://localhost:8000/mcpo/post_env \
  -H "Content-Type: application/json" \
  -d '{"env_vars": {"NEW_API_KEY": "secret-value"}}'

# Install a Python package
curl -X POST http://localhost:8000/mcpo/install_python_package \
  -H "Content-Type: application/json" \
  -d '{"package_name": "requests"}'
```

## State Persistence

MCPO automatically persists server and tool enable/disable states to prevent loss on restart:

- **State File:** `{config_name}_state.json` (e.g., `mcpo_state.json`)
- **Auto-saved:** On every enable/disable operation
- **Auto-loaded:** On server startup

## Troubleshooting

### Common Issues

1. **Port already in use:**
   ```bash
   # Kill processes on port 8000
   # Windows:
   netstat -ano | findstr :8000
   taskkill /F /PID <PID>
   
   # Linux/Mac:
   lsof -i :8000
   kill <PID>
   ```

2. **Module not found:**
   ```bash
   # Ensure you're using the venv Python
   which python  # Should point to venv/bin/python
   pip list | grep mcpo  # Should show MCPO installed
   ```

3. **Configuration errors:**
   ```bash
   # Validate JSON syntax
   python -m json.tool mcpo.json
   
   # Check logs
   tail -f logs/mcpo.log  # If logging to file
   ```

### Log Locations

- **Console:** All logs appear in the terminal
- **UI:** Live logs available in the web interface
- **API:** Access logs via `/mcpo/get_logs` endpoint

## Security Considerations

1. **API Keys:** Always use environment variables for sensitive data
2. **Network:** Consider running behind a reverse proxy (nginx, Apache)
3. **Firewall:** Restrict access to necessary ports only
4. **Updates:** Keep MCPO and dependencies updated

## Performance Tuning

1. **Tool Limit:** Monitor the 40+ tool warning in the UI
2. **Memory:** Each MCP server consumes memory; monitor usage
3. **Timeouts:** Adjust `--tool-timeout` for slow tools
4. **Concurrency:** MCPO handles multiple requests concurrently

## Support

For issues and questions:

1. Check the logs first
2. Verify configuration syntax
3. Test individual MCP servers
4. Review the OpenAPI documentation at `/docs`
