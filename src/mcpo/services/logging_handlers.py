"""Shared logging handlers for UI log capture."""
import logging
from datetime import datetime
from typing import Callable, List, Optional

from .logging import LogLevel, get_log_manager


SourceResolver = Callable[[logging.LogRecord, str], Optional[str]]


class BufferedLogHandler(logging.Handler):
    """Logging handler that mirrors log output into the UI log buffer."""

    def __init__(
        self,
        log_buffer: List[dict],
        log_lock,
        *,
        default_source: str = "openapi",
        source_resolver: SourceResolver | None = None,
        max_entries: int = 2000,
    ) -> None:
        super().__init__()
        self.log_buffer = log_buffer
        self.log_lock = log_lock
        self.default_source = default_source
        self.source_resolver = source_resolver
        self._max_entries = max_entries

    # Basic heuristics for log categorisation to match legacy UI tabs
    def _categorize(self, record: logging.LogRecord, message: str) -> str:
        msg_lower = message.lower()
        if record.levelname == "ERROR":
            return "errors"
        if "http" in msg_lower:
            return "http"
        if "tool" in msg_lower:
            return "tools"
        if "session" in msg_lower:
            return "sessions"
        if "connect" in msg_lower or "health" in msg_lower:
            return "health"
        if any(word in msg_lower for word in ("performance", "slow", "timeout")):
            return "performance"
        return "system"

    def _resolve_source(self, record: logging.LogRecord, message: str) -> str:
        if self.source_resolver:
            try:
                resolved = self.source_resolver(record, message)
                if resolved:
                    return resolved
            except Exception:
                pass
        return self.default_source

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Preserve raw message for UI readability; self.format may include timestamps already
            message = record.getMessage()
            category = self._categorize(record, message)
            source = self._resolve_source(record, message)
            timestamp_str = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
            level_map = {
                "DEBUG": LogLevel.DEBUG,
                "INFO": LogLevel.INFO,
                "WARNING": LogLevel.WARNING,
                "ERROR": LogLevel.ERROR,
                "CRITICAL": LogLevel.CRITICAL,
            }
            log_manager = get_log_manager()
            log_level = level_map.get(record.levelname, LogLevel.INFO)
            try:
                log_entry = log_manager.add_log(
                    message,
                    log_level,
                    category,
                    source=source,
                    timestamp=timestamp_str,
                    logger=record.name,
                )
                serialized = log_entry.to_dict()
            except Exception:
                # If log manager fails, fall back to a minimal entry without sequence metadata
                serialized = {
                    "timestamp": timestamp_str,
                    "level": record.levelname,
                    "message": message,
                    "category": category,
                    "source": source,
                }
                if record.name:
                    serialized["logger"] = record.name

            with self.log_lock:
                self.log_buffer.append(serialized)
                if len(self.log_buffer) > self._max_entries:
                    self.log_buffer.pop(0)
        except Exception:
            # Defensive: never propagate logging errors
            pass


__all__ = ["BufferedLogHandler", "SourceResolver"]
