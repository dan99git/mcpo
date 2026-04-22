"""
Health endpoint router.

Exposes `/healthz` plus module-level helpers (`_health_state`,
`_update_health_snapshot`) that are mutated by main.py's reload handler
and read by the endpoint. Keeping the state at module scope preserves
the legacy singleton pattern that other code in main.py depends on.
"""
from typing import Any, Dict

from fastapi import APIRouter, FastAPI, Request
from starlette.routing import Mount

router = APIRouter()


_health_state: Dict[str, Any] = {
    "generation": 0,
    "last_reload": None,
    "servers": {},  # name -> {connected: bool, type: str}
}


def _update_health_snapshot(app: FastAPI) -> None:
    """Recompute health snapshot based on mounted sub apps."""
    servers: Dict[str, Dict[str, Any]] = {}
    for route in app.router.routes:
        if isinstance(route, Mount) and isinstance(route.app, FastAPI):
            sub_app = route.app
            servers[sub_app.title] = {
                "connected": bool(getattr(sub_app.state, "is_connected", False)),
                "type": getattr(sub_app.state, "server_type", "unknown"),
            }
    _health_state["servers"] = servers


@router.get("/healthz")
async def healthz(request: Request) -> Dict[str, Any]:
    """Basic health & connectivity info for the aggregate app."""
    _update_health_snapshot(request.app)
    return {
        "status": "ok",
        "generation": _health_state["generation"],
        "lastReload": _health_state["last_reload"],
        "servers": _health_state["servers"],
    }


def register_health_endpoint(app: FastAPI) -> None:
    """Mount the health router on the given FastAPI app.

    Kept as a helper for backward compatibility with existing call sites
    (main.py, tests/test_health_and_timeout.py).
    """
    app.include_router(router)
