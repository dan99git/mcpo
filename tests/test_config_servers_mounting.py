from fastapi import FastAPI
from mcpo.main import mount_config_servers


def test_mounts_all_servers_in_config(monkeypatch):
    app = FastAPI()
    app.state.path_prefix = "/"
    state_manager = type(
        "StateManagerStub",
        (),
        {"is_server_enabled": lambda self, _name: True},
    )()
    app.state.state_manager = state_manager

    def fail_global_state_lookup():
        raise AssertionError("global state used")

    monkeypatch.setattr("mcpo.main.get_state_manager", fail_global_state_lookup)
    cfg = {
        "mcpServers": {
            "a": {"command": "echo", "args": ["1"]},
            "b": {"type": "sse", "url": "http://localhost/sse"},
        }
    }
    mount_config_servers(
        app,
        cfg,
        cors_allow_origins=["*"],
        api_key=None,
        strict_auth=False,
        api_dependency=None,
        connection_timeout=None,
        lifespan=None,
        path_prefix=app.state.path_prefix,
    )

    paths = {getattr(r, "path", None) for r in app.router.routes}
    assert "/a" in paths
    assert "/b" in paths

