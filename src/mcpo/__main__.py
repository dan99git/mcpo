"""Module entry point for `python -m mcpo`.

Executes the Typer application defined in `mcpo.__init__` so that
`python -m mcpo ...` works equivalently to invoking the installed console script `mcpo`.
"""
from . import app

if __name__ == "__main__":  # pragma: no cover - thin wrapper
    app()
