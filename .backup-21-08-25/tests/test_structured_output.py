import types as pytypes
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import types
from mcpo.utils.main import get_tool_handler


class FakeSession:
    def __init__(self, contents):
        self._contents = contents

    async def call_tool(self, endpoint_name, arguments):
        return types.CallToolResult(content=self._contents, isError=False)


def build_app(structured=False, contents=None, endpoint_name="echo"):
    app = FastAPI()
    app.state.structured_output = structured
    handler = get_tool_handler(FakeSession(contents), endpoint_name, form_model_fields=None, response_model_fields=None)
    # mount
    app.post(f"/{endpoint_name}")(handler)
    return app


def test_unstructured_simple_text():
    contents = [types.TextContent(type="text", text="hello")]
    app = build_app(False, contents)
    client = TestClient(app)
    resp = client.post("/echo")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["result"] == "hello"


def test_structured_simple_text():
    contents = [types.TextContent(type="text", text="hello")]
    app = build_app(True, contents)
    client = TestClient(app)
    resp = client.post("/echo")
    body = resp.json()
    assert body["ok"] is True
    assert body["result"] == "hello"
    assert body["output"]["items"][0]["type"] == "text"
    assert body["output"]["items"][0]["value"] == "hello"
