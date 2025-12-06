"""
Registry service for MCP server lifecycle and configuration management.
"""
import json
import asyncio
from typing import Dict, Any, List, Optional
from pathlib import Path


class ServerConfig:
    """Configuration for an MCP server."""
    
    def __init__(self, name: str, config: Dict[str, Any]):
        self.name = name
        self.config = config
        self.server_type = config.get("type", "stdio")
        self.command = config.get("command")
        self.args = config.get("args", [])
        self.env = config.get("env", {})
        self.url = config.get("url")  # For remote servers
        self.enabled = config.get("enabled", True)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert server config to dictionary."""
        return {
            "name": self.name,
            "type": self.server_type,
            "command": self.command,
            "args": self.args,
            "env": self.env,
            "url": self.url,
            "enabled": self.enabled
        }


class ServerRegistry:
    """Manages MCP server configurations and lifecycle."""
    
    def __init__(self):
        self._servers: Dict[str, ServerConfig] = {}
        self._config_data: Optional[Dict[str, Any]] = None
    
    def load_config(self, config_path: str) -> Dict[str, Any]:
        """Load server configurations from file."""
        try:
            with open(config_path, 'r') as f:
                self._config_data = json.load(f)
            
            # Parse server configurations
            mcp_servers = self._config_data.get("mcpServers", {})
            for server_name, server_cfg in mcp_servers.items():
                self._servers[server_name] = ServerConfig(server_name, server_cfg)
            
            return self._config_data
        except Exception as e:
            raise ValueError(f"Failed to load config from {config_path}: {e}")
    
    def get_server_config(self, server_name: str) -> Optional[ServerConfig]:
        """Get configuration for a specific server."""
        return self._servers.get(server_name)
    
    def get_all_servers(self) -> Dict[str, ServerConfig]:
        """Get all server configurations."""
        return dict(self._servers)
    
    def get_server_list(self) -> List[Dict[str, Any]]:
        """Get list of all servers with their basic info."""
        servers = []
        for server_name, server_config in self._servers.items():
            servers.append({
                "name": server_name,
                "type": server_config.server_type,
                "enabled": server_config.enabled,
                "command": server_config.command,
                "url": server_config.url
            })
        return servers
    
    def add_server(self, server_name: str, config: Dict[str, Any]) -> None:
        """Add a new server configuration."""
        self._servers[server_name] = ServerConfig(server_name, config)
    
    def remove_server(self, server_name: str) -> bool:
        """Remove a server configuration."""
        if server_name in self._servers:
            del self._servers[server_name]
            return True
        return False
    
    def update_server(self, server_name: str, config: Dict[str, Any]) -> None:
        """Update an existing server configuration."""
        if server_name in self._servers:
            self._servers[server_name] = ServerConfig(server_name, config)
        else:
            self.add_server(server_name, config)
    
    def save_config(self, config_path: str) -> None:
        """Save current server configurations to file."""
        if self._config_data is None:
            self._config_data = {"mcpServers": {}}
        
        # Update config data with current server configurations
        mcp_servers = {}
        for server_name, server_config in self._servers.items():
            mcp_servers[server_name] = server_config.config
        
        self._config_data["mcpServers"] = mcp_servers
        
        with open(config_path, 'w') as f:
            json.dump(self._config_data, f, indent=2)
    
    def get_config_data(self) -> Optional[Dict[str, Any]]:
        """Get the raw configuration data."""
        return self._config_data
    
    def set_config_data(self, config_data: Dict[str, Any]) -> None:
        """Set the raw configuration data and update servers."""
        self._config_data = config_data
        self._servers.clear()
        
        mcp_servers = config_data.get("mcpServers", {})
        for server_name, server_cfg in mcp_servers.items():
            self._servers[server_name] = ServerConfig(server_name, server_cfg)


# Global instance for backward compatibility
_global_registry = None

def get_server_registry() -> ServerRegistry:
    """Get or create global server registry instance.""" 
    global _global_registry
    if _global_registry is None:
        _global_registry = ServerRegistry()
    return _global_registry
