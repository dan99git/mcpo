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

# Keep test runs out of the real debug logs: build_main_app/run_proxy attach a
# rotating file handler (services/file_logging.py), so without this every pytest
# run appends test noise into logs/openapi.log. Tests that need a specific dir
# (tests/test_file_logging.py) override MCPO_LOG_DIR via monkeypatch.
if not os.environ.get("MCPO_LOG_DIR"):
    import tempfile

    os.environ["MCPO_LOG_DIR"] = tempfile.mkdtemp(prefix="mcpo-test-logs-")
