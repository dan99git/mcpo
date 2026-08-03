"""Tests for persistent rotating file logging (src/mcpo/services/file_logging.py)."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from mcpo.services.file_logging import (
    BACKUP_COUNT,
    MAX_BYTES,
    derive_log_filename,
    reattach_uvicorn_file_handlers,
    resolve_log_dir,
    setup_file_logging,
)


@pytest.fixture(autouse=True)
def _clean_file_handlers():
    """Remove any RotatingFileHandler added during a test from all touched loggers."""
    yield
    for name in (None, "uvicorn", "uvicorn.access"):
        logger_obj = logging.getLogger(name) if name else logging.getLogger()
        for handler in list(logger_obj.handlers):
            if isinstance(handler, RotatingFileHandler):
                logger_obj.removeHandler(handler)
                handler.close()


def test_filename_serve():
    assert derive_log_filename("serve", 8000) == "openapi.log"


def test_filename_proxy_includes_port():
    assert derive_log_filename("proxy", 8001) == "proxy-8001.log"
    assert derive_log_filename("proxy", 8351) == "proxy-8351.log"


def test_log_dir_env_override(monkeypatch, tmp_path):
    custom = tmp_path / "custom-logs"
    monkeypatch.setenv("MCPO_LOG_DIR", str(custom))
    assert resolve_log_dir() == custom


def test_default_log_dir_is_repo_logs(monkeypatch):
    monkeypatch.delenv("MCPO_LOG_DIR", raising=False)
    log_dir = resolve_log_dir()
    assert log_dir.name == "logs"
    assert (log_dir.parent / "pyproject.toml").exists()


def test_handler_attaches_with_rotation_config(monkeypatch, tmp_path):
    monkeypatch.setenv("MCPO_LOG_DIR", str(tmp_path))
    handler = setup_file_logging("proxy", 18001)
    assert handler is not None
    assert handler in logging.getLogger().handlers
    assert handler.maxBytes == MAX_BYTES == 5 * 1024 * 1024
    assert handler.backupCount == BACKUP_COUNT == 3
    assert handler.encoding == "utf-8"
    assert Path(handler.baseFilename) == (tmp_path / "proxy-18001.log").resolve()


def test_writes_line_and_traceback(monkeypatch, tmp_path):
    monkeypatch.setenv("MCPO_LOG_DIR", str(tmp_path))
    handler = setup_file_logging("serve", 18000)
    assert handler is not None
    test_logger = logging.getLogger("mcpo.test_file_logging")
    test_logger.warning("file-log-smoke warning line")
    try:
        raise ValueError("file-log-smoke boom")
    except ValueError:
        test_logger.error("file-log-smoke error line", exc_info=True)
    handler.flush()

    content = (tmp_path / "openapi.log").read_text(encoding="utf-8")
    assert "file-log-smoke warning line" in content
    assert "WARNING [mcpo.test_file_logging]" in content
    assert "Traceback (most recent call last)" in content
    assert "ValueError: file-log-smoke boom" in content


def test_setup_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("MCPO_LOG_DIR", str(tmp_path))
    first = setup_file_logging("proxy", 18001)
    second = setup_file_logging("proxy", 18001)
    assert first is second
    root_file_handlers = [
        h
        for h in logging.getLogger().handlers
        if isinstance(h, RotatingFileHandler)
        and h.baseFilename == first.baseFilename
    ]
    assert len(root_file_handlers) == 1


def test_failure_warns_on_stderr_and_returns_none(monkeypatch, tmp_path, capsys):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("occupied", encoding="utf-8")
    # MCPO_LOG_DIR points at an existing FILE -> mkdir fails -> loud warning, no crash
    monkeypatch.setenv("MCPO_LOG_DIR", str(blocker))
    handler = setup_file_logging("proxy", 18001)
    assert handler is None
    captured = capsys.readouterr()
    assert "file logging DISABLED" in captured.err


def test_reattach_uvicorn_file_handlers(monkeypatch, tmp_path):
    monkeypatch.setenv("MCPO_LOG_DIR", str(tmp_path))
    handler = setup_file_logging("proxy", 18001)
    assert handler is not None
    reattach_uvicorn_file_handlers()
    assert handler in logging.getLogger("uvicorn").handlers
    assert handler in logging.getLogger("uvicorn.access").handlers
    # Calling again must not duplicate
    reattach_uvicorn_file_handlers()
    assert logging.getLogger("uvicorn").handlers.count(handler) == 1
