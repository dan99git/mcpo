import pytest
from mcpo.models.config import AppConfig, ServerConfig

def test_unique_names():
    with pytest.raises(ValueError):
        AppConfig(servers=[
            ServerConfig(name="a", type="stdio", command="echo"),
            ServerConfig(name="a", type="stdio", command="echo")
        ])

def test_stdio_requires_command():
    with pytest.raises(ValueError):
        ServerConfig(name="x", type="stdio")

def test_remote_requires_url():
    with pytest.raises(ValueError):
        ServerConfig(name="sse1", type="sse")

def test_valid_streamable_http():
    cfg = ServerConfig(name="api", type="streamable-http", url="http://localhost:8000")
    assert cfg.url.endswith(":8000")
