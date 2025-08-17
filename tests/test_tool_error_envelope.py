from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import types
from mcpo.utils.main import get_tool_handler


class ErrorSession:
    def __init__(self, message: str):
        self._message = message

    async def call_tool(self, endpoint_name, arguments):
        # Return an error style CallToolResult
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=self._message)],
            isError=True,
        )


def build_app(structured=False, message="boom", endpoint_name="fail"):
    app = FastAPI()
    app.state.structured_output = structured
    handler = get_tool_handler(ErrorSession(message), endpoint_name, form_model_fields=None, response_model_fields=None)
    app.post(f"/{endpoint_name}")(handler)
    return app


def test_unstructured_error_envelope():
    app = build_app(False, "explode")
    client = TestClient(app)
    resp = client.post("/fail")
    assert resp.status_code == 500
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["message"] == "explode"
    assert "output" not in body


def test_structured_error_envelope():
    app = build_app(True, "explode-2")
    client = TestClient(app)
    resp = client.post("/fail")
    assert resp.status_code == 500
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["message"] == "explode-2"
    assert body["output"]["type"] == "collection"
    assert body["output"]["items"] == []
