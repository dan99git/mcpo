"""
CLI commands and command line interface for MCPO.
Provides a clean, organized structure for all CLI operations.
"""

import os
import sys
import asyncio
from typing import Optional, List

import typer
from typing_extensions import Annotated

from .commands import ConfigManager, ServerRunner


# Create the main CLI app
cli = typer.Typer(
    name="mcpo",
    help="MCP OpenAPI Proxy - Convert MCP servers to HTTP APIs",
    add_completion=False
)


@cli.command(name="serve", help="Start the MCPO proxy server")
def serve_command(
    # Server configuration
    host: Annotated[str, typer.Option("--host", "-h", help="Host address")] = "0.0.0.0",
    port: Annotated[int, typer.Option("--port", "-p", help="Port number")] = 8000,
    
    # Authentication
    api_key: Annotated[Optional[str], typer.Option("--api-key", "-k", help="API key for authentication")] = None,
    strict_auth: Annotated[bool, typer.Option("--strict-auth", help="API key protects all endpoints")] = False,
    
    # CORS and networking
    cors_allow_origins: Annotated[List[str], typer.Option("--cors-allow-origins", help="CORS allowed origins")] = ["*"],
    
    # SSL/TLS
    ssl_certfile: Annotated[Optional[str], typer.Option("--ssl-certfile", help="SSL certificate file")] = None,
    ssl_keyfile: Annotated[Optional[str], typer.Option("--ssl-keyfile", help="SSL private key file")] = None,
    
    # Configuration
    config_path: Annotated[Optional[str], typer.Option("--config", "-c", help="Configuration file path")] = None,
    hot_reload: Annotated[bool, typer.Option("--hot-reload", help="Enable hot reload for config changes")] = False,
    
    # Environment
    env_path: Annotated[Optional[str], typer.Option("--env-path", help="Environment variables file")] = None,
    
    # Server metadata
    name: Annotated[Optional[str], typer.Option("--name", "-n", help="Server name")] = None,
    description: Annotated[Optional[str], typer.Option("--description", "-d", help="Server description")] = None,
    version: Annotated[Optional[str], typer.Option("--version", "-V", help="Server version")] = None,
    
    # Advanced options
    path_prefix: Annotated[Optional[str], typer.Option("--path-prefix", help="URL path prefix")] = None,
    server_type: Annotated[str, typer.Option("--server-type", help="MCP server type")] = "stdio",
    
    # Logging
    log_level: Annotated[str, typer.Option("--log-level", "-l", help="Log level (debug, info, warning, error)")] = "info",
):
    """Start the MCPO proxy server with the specified configuration."""
    
    # Determine configuration
    config_path, server_command = ServerRunner.determine_server_config(config_path, sys.argv)
    
    # Validate configuration
    if not ServerRunner.validate_server_config(config_path, server_command):
        raise typer.Exit(1)
    
    # Set up environment
    ServerRunner.setup_environment(env_path)
    
    # Normalize path prefix
    path_prefix = ServerRunner.normalize_path_prefix(path_prefix)
    
    # Display startup info
    ServerRunner.display_startup_info(config_path, server_command, host, port)
    
    # Start the server
    asyncio.run(
        ServerRunner.start_server(
            host=host,
            port=port,
            api_key=api_key,
            strict_auth=strict_auth,
            cors_allow_origins=cors_allow_origins,
            server_type=server_type,
            config_path=config_path,
            server_command=server_command,
            name=name,
            description=description,
            version=version,
            ssl_certfile=ssl_certfile,
            ssl_keyfile=ssl_keyfile,
            path_prefix=path_prefix,
            hot_reload=hot_reload,
            log_level=log_level,
        )
    )


@cli.command(name="config", help="Configuration management")
def config_command(
    action: Annotated[str, typer.Argument(help="Action: validate, create, show")],
    config_path: Annotated[str, typer.Option("--config", "-c", help="Configuration file path")] = "mcpo.json",
    force: Annotated[bool, typer.Option("--force", "-f", help="Force overwrite existing files")] = False,
):
    """Manage MCPO configuration files."""
    
    config_manager = ConfigManager()
    success = False
    
    if action == "validate":
        success = config_manager.validate_config(config_path)
    elif action == "create":
        success = config_manager.create_sample_config(config_path, force)
    elif action == "show":
        success = config_manager.show_config(config_path)
    else:
        typer.echo(f"❌ Unknown action: {action}")
        typer.echo("Available actions: validate, create, show")
    
    if not success:
        raise typer.Exit(1)


@cli.command(name="version", help="Show version information")
def version_command():
    """Display version information."""
    try:
        import importlib.metadata
        version = importlib.metadata.version("mcpo")
    except Exception:
        version = "unknown"
    
    typer.echo(f"MCPO version: {version}")


# Make the default command be 'serve' for backward compatibility
@cli.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """MCP OpenAPI Proxy - Convert MCP servers to HTTP APIs."""
    if ctx.invoked_subcommand is None:
        # No subcommand was provided, default to serve with original behavior
        # This maintains backward compatibility with the old CLI
        ctx.invoke(serve_command)


if __name__ == "__main__":
    cli()
