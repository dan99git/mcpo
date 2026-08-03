"""Persistent rotating file logging for mcpo processes.

Attaches a ``RotatingFileHandler`` to the root logger so faults and activity
survive restarts (the in-memory UI buffer in ``logging.py`` is lost on every
restart). File name derives from process mode + port so the three processes
started by start.bat never collide:

- serve (admin API)      -> logs/openapi.log
- proxy on port 8001     -> logs/proxy-8001.log
- OAuth proxy on 8351    -> logs/proxy-8351.log

Log directory: ``MCPO_LOG_DIR`` env var if set, else ``<repo>/logs``.
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
BACKUP_COUNT = 3


def derive_log_filename(mode: str, port: int) -> str:
    """Derive the log file name from process mode and port."""
    if mode == "serve":
        return "openapi.log"
    return f"{mode}-{port}.log"


def resolve_log_dir() -> Path:
    """Resolve the log directory: MCPO_LOG_DIR env override, else <repo>/logs."""
    env_dir = os.environ.get("MCPO_LOG_DIR")
    if env_dir:
        return Path(env_dir).expanduser()
    # src/mcpo/services/file_logging.py -> repo root is parents[3]
    candidate = Path(__file__).resolve().parents[3]
    if (candidate / "pyproject.toml").exists():
        return candidate / "logs"
    return Path.cwd() / "logs"


def setup_file_logging(mode: str, port: int) -> Optional[RotatingFileHandler]:
    """Attach a rotating file handler to the root logger.

    Idempotent: a second call targeting the same file returns the existing
    handler. On failure prints a loud stderr warning and returns None; the
    process keeps running with console/UI logging only (no silent failure,
    no crash).
    """
    try:
        log_dir = resolve_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / derive_log_filename(mode, port)
        resolved = str(log_path.resolve())

        root_logger = logging.getLogger()
        for handler in root_logger.handlers:
            if (
                isinstance(handler, RotatingFileHandler)
                and getattr(handler, "baseFilename", None) == resolved
            ):
                return handler

        handler = RotatingFileHandler(
            resolved,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root_logger.addHandler(handler)
        return handler
    except Exception as exc:  # pragma: no cover - exercised via tests with bad dir
        print(
            f"WARNING: mcpo file logging DISABLED ({mode}:{port}): "
            f"cannot open log file: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return None


def reattach_uvicorn_file_handlers() -> None:
    """Re-attach root file handlers to uvicorn's non-propagating loggers.

    uvicorn's default dictConfig (applied inside ``uvicorn.Config.__init__``)
    sets ``propagate=False`` on ``uvicorn``/``uvicorn.access`` and clears any
    handlers previously attached to them, so their records never reach the
    root logger. Call this AFTER constructing ``uvicorn.Config`` so access
    and error lines land in the log file as well.
    """
    root_logger = logging.getLogger()
    file_handlers = [
        h for h in root_logger.handlers if isinstance(h, RotatingFileHandler)
    ]
    if not file_handlers:
        return
    # "uvicorn.error" propagates to "uvicorn" (which has propagate=False), so
    # attaching to "uvicorn" covers error records without double-writing them.
    for logger_name in ("uvicorn", "uvicorn.access"):
        logger_obj = logging.getLogger(logger_name)
        for handler in file_handlers:
            if handler not in logger_obj.handlers:
                logger_obj.addHandler(handler)


__all__ = [
    "BACKUP_COUNT",
    "LOG_FORMAT",
    "MAX_BYTES",
    "derive_log_filename",
    "reattach_uvicorn_file_handlers",
    "resolve_log_dir",
    "setup_file_logging",
]
