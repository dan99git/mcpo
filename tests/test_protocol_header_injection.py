from fastapi import FastAPI
from mcpo.main import create_sub_app, MCP_VERSION


def test_sse_headers_inject_protocol_version():
    sub = create_sub_app(
        server_name="sse1",
        server_cfg={"type": "sse", "url": "http://localhost/sse", "headers": {"Authorization": "x"}},
        cors_allow_origins=["*"],
        api_key=None,
        strict_auth=False,
        api_dependency=None,
        connection_timeout=10,
        lifespan=None,
    )
    headers = getattr(sub.state, "headers", {})
    assert headers["MCP-Protocol-Version"] == MCP_VERSION
    assert headers["Authorization"] == "x"


def test_streamable_http_headers_inject_protocol_version():
    sub = create_sub_app(
        server_name="http1",
        server_cfg={"type": "streamable-http", "url": "http://localhost/mcp"},
        cors_allow_origins=["*"],
        api_key=None,
        strict_auth=False,
        api_dependency=None,
        connection_timeout=10,
        lifespan=None,
    )
    headers = getattr(sub.state, "headers", {})
    assert headers["MCP-Protocol-Version"] == MCP_VERSION

