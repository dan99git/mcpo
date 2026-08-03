from fastapi import FastAPI
from mcpo.main import create_sub_app


def test_streamable_http_type_normalized():
    app = FastAPI()
    sub = create_sub_app(
        server_name="x",
        server_cfg={"type": "streamable-http", "url": "http://localhost/mcp"},
        cors_allow_origins=["*"],
        api_key=None,
        strict_auth=False,
        api_dependency=None,
        connection_timeout=10,
        lifespan=None,
    )
    assert getattr(sub.state, "server_type") == "streamable-http"

