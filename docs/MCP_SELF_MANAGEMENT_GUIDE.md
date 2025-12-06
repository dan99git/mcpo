# 🤖 MCP Self-Management Guide

**API reference for managing MCP servers programmatically**

OpenHubUI exposes management endpoints that allow scripts or AI models to add, remove, enable, and disable MCP servers and tools without editing config files.

> **Prefer the UI?** Visit `http://localhost:8000/ui` for visual management.

---

## 🎯 What You Can Do

| Action | Endpoint | Method |
|--------|----------|--------|
| List servers | `/_meta/servers` | GET |
| List tools | `/_meta/servers/{name}/tools` | GET |
| Enable/disable server | `/_meta/servers/{name}/enable` | POST |
| Enable/disable tool | `/_meta/servers/{name}/tools/{tool}/enable` | POST |
| Get config | `/_meta/config/content` | GET |
| Save config | `/_meta/config/save` | POST |
| Reload servers | `/_meta/reload` | POST |

---

## 🔍 Discovery & Inspection

### List All Servers
```bash
GET /_meta/servers
```

**Response:**
```json
{
  "servers": {
    "time": {
      "name": "time",
      "type": "stdio",
      "connected": true,
      "enabled": true,
      "basePath": "/time",
      "toolCount": 3
    },
    "playwright": {
      "name": "playwright", 
      "type": "stdio",
      "connected": true,
      "enabled": true,
      "basePath": "/playwright",
      "toolCount": 8
    }
  }
}
```

### Inspect Server Tools
```bash
GET /_meta/servers/{server-name}/tools
```

**Response:**
```json
{
  "tools": [
    {
      "name": "browser_navigate",
      "enabled": true,
      "description": "Navigate to a URL in the browser"
    },
    {
      "name": "browser_snapshot", 
      "enabled": false,
      "description": "Take a screenshot of the current page"
    }
  ]
}
```

### Get System Health
```bash
GET /_meta/metrics
```

**Response:**
```json
{
  "version": "1.0.0-rc1",
  "uptime": 3600,
  "servers": {
    "total": 5,
    "connected": 4,
    "enabled": 3
  },
  "tools": {
    "total": 25,
    "enabled": 18
  }
}
```

---

## ⚙️ Configuration Management

### Get Current Configuration
```bash
GET /_meta/config/content
```

**Response:** Raw `mcpo.json` content as string

### Update Configuration
```bash
POST /_meta/config/save
Content-Type: application/json

{
  "content": "{\n  \"mcpServers\": {\n    \"new-server\": {\n      \"command\": \"npx\",\n      \"args\": [\"-y\", \"@my/mcp-server\"],\n      \"enabled\": true\n    }\n  }\n}"
}
```

**Response:**
```json
{
  "ok": true,
  "message": "Configuration saved and servers reloaded",
  "backup": "mcpo.json.backup.2024-01-15T10:30:00Z"
}
```

### Reload Servers
```bash
POST /_meta/reload
```

**Response:**
```json
{
  "ok": true,
  "message": "Configuration reloaded",
  "servers": {
    "added": ["new-server"],
    "removed": [],
    "updated": []
  }
}
```

---

## 📦 Dependency Management

### Get Current Dependencies
```bash
GET /_meta/requirements/content
```

**Response:** Raw `requirements.txt` content as string

### Update Dependencies
```bash
POST /_meta/requirements/save
Content-Type: application/json

{
  "content": "requests==2.31.0\nbeautifulsoup4==4.12.2\nselenium==4.15.0"
}
```

**Response:**
```json
{
  "ok": true,
  "message": "Dependencies installed and servers reloaded",
  "packages": ["requests==2.31.0", "beautifulsoup4==4.12.2", "selenium==4.15.0"]
}
```

---

## 🎛️ Server Control

### Enable/Disable Server
```bash
POST /_meta/servers/{server-name}/enable
POST /_meta/servers/{server-name}/disable
```

### Enable/Disable Individual Tools
```bash
POST /_meta/servers/{server-name}/tools/{tool-name}/enable
POST /_meta/servers/{server-name}/tools/{tool-name}/disable
```

### Reinitialize Server
```bash
POST /_meta/reinit/{server-name}
```

**Use when:** Server is stuck, needs fresh connection, or after dependency changes.

---

## 🤖 AI Model Workflow Examples

### Example 1: Add a New MCP Server

```python
import requests
import json

def add_mcp_server(server_name, command, args, env=None):
    """Add a new MCP server to the configuration."""
    
    # 1. Get current configuration
    config_resp = requests.get("http://localhost:8000/_meta/config/content")
    current_config = json.loads(config_resp.text)
    
    # 2. Add new server
    new_server = {
        "command": command,
        "args": args,
        "enabled": True
    }
    if env:
        new_server["env"] = env
    
    current_config["mcpServers"][server_name] = new_server
    
    # 3. Save configuration
    save_resp = requests.post(
        "http://localhost:8000/_meta/config/save",
        json={"content": json.dumps(current_config, indent=2)}
    )
    
    return save_resp.json()

# Usage
result = add_mcp_server(
    server_name="github",
    command="npx",
    args=["-y", "@modelcontextprotocol/server-github"],
    env={"GITHUB_PERSONAL_ACCESS_TOKEN": "your-token"}
)
```

### Example 2: Install Dependencies for a Server

```python
def install_dependencies(packages):
    """Install Python packages and reload servers."""
    
    # 1. Get current requirements
    req_resp = requests.get("http://localhost:8000/_meta/requirements/content")
    current_reqs = req_resp.text.strip()
    
    # 2. Add new packages
    new_packages = "\n".join(packages)
    updated_reqs = current_reqs + "\n" + new_packages if current_reqs else new_packages
    
    # 3. Save requirements
    save_resp = requests.post(
        "http://localhost:8000/_meta/requirements/save",
        json={"content": updated_reqs}
    )
    
    return save_resp.json()

# Usage
result = install_dependencies([
    "requests==2.31.0",
    "beautifulsoup4==4.12.2"
])
```

### Example 3: Filter Tools Based on Task

```python
def filter_tools_for_task(server_name, task_type):
    """Enable only relevant tools for a specific task."""
    
    # 1. Get available tools
    tools_resp = requests.get(f"http://localhost:8000/_meta/servers/{server_name}/tools")
    tools = tools_resp.json()["tools"]
    
    # 2. Define task-specific tool patterns
    task_patterns = {
        "web_scraping": ["browser_", "navigate", "click", "extract"],
        "file_management": ["read", "write", "list", "delete"],
        "data_analysis": ["query", "analyze", "transform", "visualize"]
    }
    
    patterns = task_patterns.get(task_type, [])
    
    # 3. Enable/disable tools based on patterns
    for tool in tools:
        tool_name = tool["name"]
        should_enable = any(pattern in tool_name for pattern in patterns)
        
        endpoint = f"http://localhost:8000/_meta/servers/{server_name}/tools/{tool_name}"
        if should_enable:
            requests.post(f"{endpoint}/enable")
        else:
            requests.post(f"{endpoint}/disable")
    
    return f"Filtered tools for {task_type} task"

# Usage
filter_tools_for_task("playwright", "web_scraping")
```

### Example 4: Complete Server Setup Workflow

```python
def setup_complete_workflow():
    """Complete workflow: discover, configure, and optimize."""
    
    # 1. Check system health
    health = requests.get("http://localhost:8000/_meta/metrics").json()
    print(f"System health: {health}")
    
    # 2. List available servers
    servers = requests.get("http://localhost:8000/_meta/servers").json()
    print(f"Available servers: {list(servers['servers'].keys())}")
    
    # 3. Add a new server if needed
    if "memory" not in servers["servers"]:
        add_mcp_server(
            "memory",
            "npx", 
            ["-y", "@modelcontextprotocol/server-memory"]
        )
    
    # 4. Install required dependencies
    install_dependencies(["psutil==5.9.0"])
    
    # 5. Filter tools for current task
    filter_tools_for_task("playwright", "web_scraping")
    
    # 6. Verify setup
    final_health = requests.get("http://localhost:8000/_meta/metrics").json()
    print(f"Final setup: {final_health}")
    
    return "Setup complete!"

# Usage
setup_complete_workflow()
```

---

## 🔒 Security Considerations

### API Key Protection
```bash
# Always use API keys in production
curl -H "Authorization: Bearer your-api-key" \
     http://localhost:8000/_meta/servers
```

### Environment Variable Management
```python
# Safely add environment variables
def add_env_var(server_name, key, value):
    config_resp = requests.get("http://localhost:8000/_meta/config/content")
    config = json.loads(config_resp.text)
    
    if "env" not in config["mcpServers"][server_name]:
        config["mcpServers"][server_name]["env"] = {}
    
    config["mcpServers"][server_name]["env"][key] = value
    
    requests.post(
        "http://localhost:8000/_meta/config/save",
        json={"content": json.dumps(config, indent=2)}
    )
```

---

## 📊 Monitoring & Debugging

### Check Server Logs
```bash
GET /_meta/logs
```

### Reinitialize Stuck Server
```bash
POST /_meta/reinit/{server-name}
```

### Validate Configuration
```bash
POST /_meta/config/validate
```

---

## 🎯 Best Practices

1. **Always backup before changes**: The system creates automatic backups
2. **Test in development first**: Use a separate instance for experimentation
3. **Monitor tool counts**: Too many tools can impact performance
4. **Use environment variables**: Never hardcode secrets in configuration
5. **Filter tools appropriately**: Enable only what you need for the task
6. **Check health regularly**: Monitor server status and tool availability

---

## 🚀 Advanced Use Cases

### Dynamic Tool Discovery
```python
def discover_and_add_tools():
    """Discover new MCP servers from a registry and add them."""
    # Implementation for discovering servers from npm/pypi
    pass
```

### Load Balancing
```python
def balance_tool_load():
    """Distribute tools across multiple servers for performance."""
    # Implementation for load balancing
    pass
```

### Auto-Scaling
```python
def auto_scale_servers():
    """Automatically add/remove servers based on usage."""
    # Implementation for auto-scaling
    pass
```

---

This self-management system transforms OpenHubUI from a static MCP proxy into a **dynamic, AI-manageable platform** that can adapt and grow with your needs.
