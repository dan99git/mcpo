import sys
import os

# Optional: ensure src on sys.path for direct test execution contexts
ROOT = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(ROOT), 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)

# Attempt to import pytest_asyncio to activate plugin (ignore if unavailable)
try:  # pragma: no cover
    import pytest_asyncio  # noqa: F401
except Exception:  # pragma: no cover
    pass
