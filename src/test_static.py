#!/usr/bin/env python3

import os
from pathlib import Path

# Test static path resolution from this directory
print("Current working directory:", os.getcwd())
print("Script directory:", Path(__file__).parent)

static_path = "../static/ui"
print(f"Checking path: {static_path}")
print(f"Path exists: {os.path.exists(static_path)}")
print(f"Is directory: {os.path.isdir(static_path)}")

if os.path.exists(static_path):
    print("Contents of static/ui:")
    for item in os.listdir(static_path):
        print(f"  - {item}")

# Test FastAPI StaticFiles import
try:
    from fastapi.staticfiles import StaticFiles
    print("\nTesting StaticFiles creation:")
    static_files = StaticFiles(directory=static_path, html=True)
    print(f"StaticFiles created successfully with directory: {static_files.directory}")
except Exception as e:
    print(f"Error creating StaticFiles: {e}")
