"""The UI log buffer must keep receiving uvicorn lines after uvicorn's
dictConfig wipes handler attachments (reattach_uvicorn_file_handlers covers
BufferedLogHandler, not just the rotating file handler)."""
import logging
import threading

import pytest

from mcpo.services.file_logging import reattach_uvicorn_file_handlers
from mcpo.services.logging_handlers import BufferedLogHandler


@pytest.fixture()
def buffer_on_root():
    buffer: list = []
    handler = BufferedLogHandler(buffer, threading.Lock(), default_source="mcp")
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield buffer, handler
    finally:
        root.removeHandler(handler)
        for name in ("uvicorn", "uvicorn.access"):
            logging.getLogger(name).removeHandler(handler)


def _simulate_uvicorn_dictconfig_wipe():
    # uvicorn.Config.__init__ applies a dictConfig that clears handlers and
    # sets propagate=False on these loggers.
    for name in ("uvicorn", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = False
        logger.setLevel(logging.INFO)


def test_buffer_reattached_after_dictconfig_wipe(buffer_on_root):
    buffer, handler = buffer_on_root
    _simulate_uvicorn_dictconfig_wipe()

    logging.getLogger("uvicorn.access").info("before reattach - lost")
    assert len(buffer) == 0  # proves the wipe actually severs the buffer

    reattach_uvicorn_file_handlers()

    assert handler in logging.getLogger("uvicorn").handlers
    assert handler in logging.getLogger("uvicorn.access").handlers

    logging.getLogger("uvicorn.access").info('127.0.0.1 - "GET /x" 200')
    assert len(buffer) == 1


def test_reattach_is_idempotent(buffer_on_root):
    _, handler = buffer_on_root
    _simulate_uvicorn_dictconfig_wipe()

    reattach_uvicorn_file_handlers()
    reattach_uvicorn_file_handlers()

    access = logging.getLogger("uvicorn.access")
    assert access.handlers.count(handler) == 1
