#!/usr/bin/env python3
"""
MCPO Production Setup Script

This script sets up MCPO in a Python virtual environment for production deployment.
It handles dependency installation, configuration setup, and environment preparation.
"""

import os
import sys
import subprocess
import json
import shutil
import argparse
from pathlib import Path


def run_command(cmd, check=True, capture_output=False):
    """Run a shell command with error handling."""
    print(f"Running: {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    try:
        result = subprocess.run(cmd, check=check, capture_output=capture_output, text=True, shell=True)
        return result
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Command failed with exit code {e.returncode}")
        if capture_output and e.stderr:
            print(f"Error output: {e.stderr}")
        if check:
            sys.exit(1)
        return e


def create_venv(venv_path):
    """Create Python virtual environment."""
    print(f"\n📦 Creating virtual environment at {venv_path}")
    
    if os.path.exists(venv_path):
        print(f"Virtual environment already exists at {venv_path}")
        return
    
    run_command([sys.executable, "-m", "venv", venv_path])
    print(f"✅ Virtual environment created at {venv_path}")


def get_venv_python(venv_path):
    """Get the Python executable path for the venv."""
    if sys.platform == "win32":
        return os.path.join(venv_path, "Scripts", "python.exe")
    else:
        return os.path.join(venv_path, "bin", "python")


def get_venv_pip(venv_path):
    """Get the pip executable path for the venv."""
    if sys.platform == "win32":
        return os.path.join(venv_path, "Scripts", "pip.exe")
    else:
        return os.path.join(venv_path, "bin", "pip")


def install_dependencies(venv_path):
    """Install MCPO and its dependencies in the venv."""
    print(f"\n📚 Installing dependencies in virtual environment")
    
    pip_path = get_venv_pip(venv_path)
    
    # Upgrade pip first
    run_command([pip_path, "install", "--upgrade", "pip"])
    
    # Install MCPO in development mode
    run_command([pip_path, "install", "-e", "."])
    
    # Install additional requirements if they exist
    if os.path.exists("requirements.txt"):
        print("Installing additional requirements from requirements.txt")
        run_command([pip_path, "install", "-r", "requirements.txt"])
    
    print("✅ Dependencies installed successfully")


def setup_configuration(config_name="mcpo.json"):
    """Set up configuration files."""
    print(f"\n⚙️  Setting up configuration")
    
    # Copy example config if config doesn't exist
    if not os.path.exists(config_name):
        if os.path.exists(f"{config_name}.example"):
            shutil.copy(f"{config_name}.example", config_name)
            print(f"✅ Created {config_name} from example")
        else:
            # Create minimal config
            minimal_config = {
                "mcpServers": {
                    "time": {
                        "command": "uvx",
                        "args": ["mcp-server-time"],
                        "enabled": True
                    }
                }
            }
            with open(config_name, 'w') as f:
                json.dump(minimal_config, f, indent=2)
            print(f"✅ Created minimal {config_name}")
    else:
        print(f"Configuration file {config_name} already exists")
    
    # Set up .env file
    if not os.path.exists(".env"):
        if os.path.exists(".env.example"):
            shutil.copy(".env.example", ".env")
            print("✅ Created .env from example")
        else:
            # Create minimal .env
            with open(".env", 'w') as f:
                f.write("MCPO_API_KEY=change-me-in-production\n")
            print("✅ Created minimal .env file")
    else:
        print(".env file already exists")


def create_production_scripts(venv_path, config_name="mcpo.json", port=8000):
    """Create production start/stop scripts."""
    print(f"\n🚀 Creating production scripts")
    
    python_path = get_venv_python(venv_path)
    venv_name = os.path.basename(venv_path)
    
    # Windows batch file
    start_bat_content = f"""@echo off
echo Starting MCPO Production Server...
echo Virtual Environment: {venv_name}
echo Configuration: {config_name}
echo Port: {port}
echo.

"{python_path}" -m mcpo --config "{config_name}" --port {port} --hot-reload
"""
    
    with open("start_production.bat", 'w') as f:
        f.write(start_bat_content)
    
    # Linux/Mac shell script
    start_sh_content = f"""#!/bin/bash
echo "Starting MCPO Production Server..."
echo "Virtual Environment: {venv_name}"
echo "Configuration: {config_name}"
echo "Port: {port}"
echo

"{python_path}" -m mcpo --config "{config_name}" --port {port} --hot-reload
"""
    
    with open("start_production.sh", 'w') as f:
        f.write(start_sh_content)
    
    # Make shell script executable on Unix systems
    if sys.platform != "win32":
        os.chmod("start_production.sh", 0o755)
    
    # Stop script (Windows)
    stop_bat_content = f"""@echo off
echo Stopping MCPO Production Server...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :{port}') do taskkill /F /PID %%a 2>nul
echo Server stopped.
"""
    
    with open("stop_production.bat", 'w') as f:
        f.write(stop_bat_content)
    
    # Stop script (Linux/Mac)
    stop_sh_content = f"""#!/bin/bash
echo "Stopping MCPO Production Server..."
pkill -f "mcpo.*--port {port}" || echo "No MCPO processes found on port {port}"
echo "Server stopped."
"""
    
    with open("stop_production.sh", 'w') as f:
        f.write(stop_sh_content)
    
    if sys.platform != "win32":
        os.chmod("stop_production.sh", 0o755)
    
    print("✅ Production scripts created:")
    print("   - start_production.bat / start_production.sh")
    print("   - stop_production.bat / stop_production.sh")


def verify_installation(venv_path):
    """Verify the MCPO installation works."""
    print(f"\n🔍 Verifying installation")
    
    python_path = get_venv_python(venv_path)
    
    # Test import
    result = run_command([python_path, "-c", "import mcpo; print('MCPO imported successfully')"], capture_output=True)
    if result.returncode == 0:
        print("✅ MCPO module imports correctly")
    else:
        print("❌ MCPO module import failed")
        return False
    
    # Test CLI
    result = run_command([python_path, "-m", "mcpo", "--help"], capture_output=True, check=False)
    if result.returncode == 0:
        print("✅ MCPO CLI works correctly")
    else:
        print("❌ MCPO CLI failed")
        return False
    
    return True


def main():
    parser = argparse.ArgumentParser(description="Setup MCPO for production deployment")
    parser.add_argument("--venv", default="venv", help="Virtual environment directory (default: venv)")
    parser.add_argument("--config", default="mcpo.json", help="Configuration file name (default: mcpo.json)")
    parser.add_argument("--port", type=int, default=8000, help="Server port (default: 8000)")
    parser.add_argument("--skip-venv", action="store_true", help="Skip virtual environment creation")
    parser.add_argument("--skip-deps", action="store_true", help="Skip dependency installation")
    parser.add_argument("--skip-config", action="store_true", help="Skip configuration setup")
    
    args = parser.parse_args()
    
    print("🔧 MCPO Production Setup")
    print("=" * 50)
    
    venv_path = os.path.abspath(args.venv)
    
    try:
        # Create virtual environment
        if not args.skip_venv:
            create_venv(venv_path)
        
        # Install dependencies
        if not args.skip_deps:
            install_dependencies(venv_path)
        
        # Setup configuration
        if not args.skip_config:
            setup_configuration(args.config)
        
        # Create production scripts
        create_production_scripts(venv_path, args.config, args.port)
        
        # Verify installation
        if verify_installation(venv_path):
            print(f"\n🎉 MCPO Production Setup Complete!")
            print(f"   Virtual Environment: {venv_path}")
            print(f"   Configuration: {args.config}")
            print(f"   Port: {args.port}")
            print("\n📋 Next Steps:")
            print("   1. Review and customize your configuration file")
            print("   2. Update .env file with your API keys")
            print("   3. Run start_production.bat (Windows) or ./start_production.sh (Linux/Mac)")
            print(f"   4. Access the UI at http://localhost:{args.port}/mcp")
        else:
            print("\n❌ Installation verification failed")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n\n⚠️  Setup interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Setup failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
