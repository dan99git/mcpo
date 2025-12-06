import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import Field

from mcp import types
from mcpo.utils.main import get_tool_handler


class RecordingSession:
    """Fake MCP ClientSession capturing arguments passed to call_tool.

    Simulates downstream MCP server response with configurable contents.
    """

    def __init__(self, contents):
        self._contents = contents
        self.last_endpoint = None
        self.last_args = None

    async def call_tool(self, endpoint_name, arguments):  # noqa: D401
        self.last_endpoint = endpoint_name
        self.last_args = arguments
        return types.CallToolResult(content=self._contents, isError=False)


def build_app_with_tool(endpoint_name="search", contents=None):
    contents = contents or [types.TextContent(type="text", text="{\"answer\":42}")]
    session = RecordingSession(contents)

    # Define a simple input schema (one required string parameter "query")
    form_model_fields = {
        "query": (str, Field(default=..., description="Search query")),
    }

    handler = get_tool_handler(
        session=session,
        endpoint_name=endpoint_name,
        form_model_fields=form_model_fields,
        response_model_fields=None,
    )

    app = FastAPI()
    # Structured output off by default here
    app.post(f"/{endpoint_name}")(handler)
    app.state.session_obj = session  # expose for assertions
    return app


def test_tool_invocation_argument_mapping():
    """Ensure JSON body is parsed into Pydantic model and forwarded unchanged to session."""
    app = build_app_with_tool()
    client = TestClient(app)
    payload = {"query": "world news"}
    resp = client.post("/search", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Legacy unstructured result path returns flattened result
    assert body["ok"] is True
    # The fake response JSON string gets parsed by process_tool_response -> json.loads
    assert body["result"] == {"answer": 42}

    session: RecordingSession = app.state.session_obj
    assert session.last_endpoint == "search"
    assert session.last_args == payload


def test_tool_invocation_missing_required_field():
    """Missing required field should trigger FastAPI / Pydantic validation (422)."""
    app = build_app_with_tool()
    client = TestClient(app)
    resp = client.post("/search", json={})  # missing required 'query'
    assert resp.status_code == 422


def test_tool_invocation_wrong_type():
    """Wrong type for parameter should raise 422 validation error."""
    app = build_app_with_tool()
    client = TestClient(app)
    resp = client.post("/search", json={"query": 123})  # int instead of str
    assert resp.status_code == 422


def test_tool_structured_output_classification():
    """With structured_output flag, ensure classification envelope is present."""
    contents = [
        types.TextContent(type="text", text="hello world"),
        types.TextContent(type="text", text="7"),
    ]
    session = RecordingSession(contents)
    form_model_fields = {"query": (str, Field(default=..., description="Q"))}
    handler = get_tool_handler(session, "analyze", form_model_fields, None)
    app = FastAPI()
    app.state.structured_output = True
    app.post("/analyze")(handler)
    client = TestClient(app)
    resp = client.post("/analyze", json={"query": "x"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    # Current behavior: legacy 'result' returns list of primitive values extracted
    assert isinstance(body["result"], list)
    assert body["result"][0] == "hello world"
    assert body["result"][1] == 7
    assert body["output"]["type"] == "collection"
    items = body["output"]["items"]
    assert len(items) == 2
    assert items[0]["type"] == "text"
    assert items[0]["value"] == "hello world"
    # Second value '7' classified as scalar (numeric string -> int) in current logic
    assert items[1]["type"] == "scalar"
    assert items[1]["value"] == 7
