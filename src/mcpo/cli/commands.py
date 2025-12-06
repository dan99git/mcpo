"""
CLI command implementations for MCPO.
Separates command logic from CLI argument parsing.
"""

import os
import json
import asyncio
from typing import Optional, List, Dict, Any

import typer
from dotenv import load_dotenv

from mcpo.main import run, load_config


class ConfigManager:
    """Manages configuration file operations."""
    
    @staticmethod
    def validate_config(config_path: str) -> bool:
        """Validate a configuration file."""
        try:
            config_data = load_config(config_path)
            typer.echo(f"✅ Configuration file {config_path} is valid")
            
            servers = config_data.get("mcpServers", {})
            typer.echo(f"Found {len(servers)} server(s):")
            
            for name, cfg in servers.items():
                server_type = cfg.get("type", "stdio")
                if server_type == "stdio":
                    cmd_info = f"command: {cfg.get('command')}"
                    if cfg.get('args'):
                        cmd_info += f" {' '.join(cfg['args'])}"
                else:
                    cmd_info = f"url: {cfg.get('url')}"
                
                typer.echo(f"  - {name} ({server_type}): {cmd_info}")
            
            return True
            
        except Exception as e:
            typer.echo(f"❌ Configuration validation failed: {e}")
            return False
    
    @staticmethod
    def create_sample_config(config_path: str, force: bool = False) -> bool:
        """Create a sample configuration file."""
        if os.path.exists(config_path) and not force:
            if not typer.confirm(f"Configuration file {config_path} exists. Overwrite?"):
                typer.echo("Configuration creation cancelled.")
                return False
        
        sample_config = {
            "mcpServers": {
                "example-stdio": {
                    "type": "stdio",
                    "command": "python",
                    "args": ["-m", "your_mcp_server"],
                    "env": {
                        "API_KEY": "${API_KEY}",
                        "DEBUG": "true"
                    }
                },
                "example-sse": {
                    "type": "sse",
                    "url": "https://api.example.com/mcp/sse",
                    "headers": {
                        "Authorization": "Bearer ${MCP_TOKEN}",
                        "User-Agent": "MCPO/1.0"
                    }
                },
                "example-http": {
                    "type": "streamable-http", 
                    "url": "https://api.example.com/mcp/streamable",
                    "headers": {
                        "X-API-Key": "${HTTP_API_KEY}"
                    }
                }
            }
        }
        
        try:
            with open(config_path, "w") as f:
                json.dump(sample_config, f, indent=2)
            
            typer.echo(f"✅ Sample configuration created: {config_path}")
            typer.echo("Edit the file to configure your MCP servers.")
            typer.echo("Environment variables like ${API_KEY} will be expanded at runtime.")
            return True
            
        except Exception as e:
            typer.echo(f"❌ Failed to create configuration: {e}")
            return False
    
    @staticmethod
    def show_config(config_path: str) -> bool:
        """Display configuration file contents."""
        try:
            with open(config_path, "r") as f:
                config_data = json.load(f)
            
            typer.echo(f"Configuration from {config_path}:")
            typer.echo(json.dumps(config_data, indent=2))
            return True
            
        except FileNotFoundError:
            typer.echo(f"❌ Configuration file not found: {config_path}")
            return False
        except Exception as e:
            typer.echo(f"❌ Failed to read configuration: {e}")
            return False


class ServerRunner:
    """Handles server startup and configuration."""
    
    @staticmethod
    def determine_server_config(config_path: Optional[str], args: List[str]) -> tuple[Optional[str], Optional[List[str]]]:
        """Determine server configuration from args and config file."""
        server_command = None
        
        # Try to find config file
        if not config_path:
            # Check for default config files
            for default_config in ["mcpo.json", "mcpp.json"]:
                if os.path.exists(default_config):
                    config_path = default_config
                    typer.echo(f"Using default config file: {config_path}")
                    break
        
        # Extract command from args if "--" separator present
        if "--" in args:
            idx = args.index("--")
            server_command = args[idx + 1:]
            
        return config_path, server_command
    
    @staticmethod
    def validate_server_config(config_path: Optional[str], server_command: Optional[List[str]]) -> bool:
        """Validate that we have either a config file or server command."""
        if not config_path and not server_command:
            typer.echo("❌ Error: Must specify either --config or provide MCP command after '--'")
            typer.echo("")
            typer.echo("Examples:")
            typer.echo("  mcpo serve --config mcpo.json")
            typer.echo("  mcpo serve --host 0.0.0.0 --port 8000 -- python -m my_mcp_server")
            typer.echo("  mcpo serve -- npx @modelcontextprotocol/server-filesystem /path/to/files")
            return False
        
        return True
    
    @staticmethod
    def setup_environment(env_path: Optional[str]) -> None:
        """Set up environment variables."""
        if env_path:
            if os.path.exists(env_path):
                load_dotenv(env_path)
                typer.echo(f"Loaded environment from: {env_path}")
            else:
                typer.echo(f"⚠️  Warning: Environment file not found: {env_path}")
    
    @staticmethod
    def normalize_path_prefix(path_prefix: Optional[str]) -> str:
        """Normalize URL path prefix."""
        if not path_prefix:
            return "/"
        
        if not path_prefix.startswith("/"):
            path_prefix = f"/{path_prefix}"
        if not path_prefix.endswith("/"):
            path_prefix = f"{path_prefix}/"
            
        return path_prefix
    
    @staticmethod
    def display_startup_info(
        config_path: Optional[str], 
        server_command: Optional[List[str]], 
        host: str, 
        port: int
    ) -> None:
        """Display server startup information."""
        if config_path:
            typer.echo(f"🚀 Starting MCPO with config: {config_path}")
        else:
            typer.echo(f"🚀 Starting MCPO on {host}:{port}")
            typer.echo(f"   Command: {' '.join(server_command)}")
        
        typer.echo(f"   Server will be available at: http://{host}:{port}")
        typer.echo(f"   API documentation at: http://{host}:{port}/docs")
    
    @staticmethod
    async def start_server(
        host: str,
        port: int,
        config_path: Optional[str] = None,
        server_command: Optional[List[str]] = None,
        **kwargs
    ) -> None:
        """Start the MCPO server."""
        await run(
            host=host,
            port=port,
            config_path=config_path,
            server_command=server_command,
            **kwargs
        )
