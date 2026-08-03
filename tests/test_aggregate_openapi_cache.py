from __future__ import annotations

import copy
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mcpo.services.state import StateManager


def _tool_spec(
    *,
    title: str,
    schemas: dict,
    request_schema: str = "Shared",
) -> dict:
    return {
        "openapi": "3.1.0",
        "info": {"title": title, "version": "1.0"},
        "paths": {
            "/tool": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": f"#/components/schemas/{request_schema}"
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        "components": {"schemas": schemas},
    }


def _mount_cached_spec(app: FastAPI, name: str, spec: dict) -> None:
    sub_app = FastAPI(title=name)
    sub_app.state.is_connected = True
    sub_app.state.config_key = name
    sub_app.openapi = lambda: spec
    app.mount(f"/{name}", sub_app)


async def _build_app(tmp_path):
    from mcpo.main import build_main_app

    state = StateManager(state_file_path=str(tmp_path / "state.json"))
    state.set_server_enabled("mcpo", False)
    with patch("mcpo.main.get_state_manager", return_value=state):
        app = await build_main_app()
    app.state.state_manager = state
    return app


@pytest.mark.asyncio
async def test_aggregate_rewrites_conflicting_refs_without_mutating_cached_specs(
    tmp_path,
) -> None:
    app = await _build_app(tmp_path)
    shared_wrapper = {
        "type": "object",
        "properties": {
            "children": {
                "type": "array",
                "items": {
                    "anyOf": [
                        {"$ref": "#/components/schemas/Nested"},
                        {"type": "null"},
                    ]
                },
            }
        },
    }
    first_spec = _tool_spec(
        title="First",
        schemas={
            "Shared": copy.deepcopy(shared_wrapper),
            "Nested": {"type": "string"},
            "Shared_two": {"type": "boolean"},
            "Nested_two": {"type": "number"},
        },
    )
    second_spec = _tool_spec(
        title="Second",
        schemas={
            "Shared": copy.deepcopy(shared_wrapper),
            "Nested": {"type": "integer"},
        },
    )
    first_original = copy.deepcopy(first_spec)
    second_original = copy.deepcopy(second_spec)
    _mount_cached_spec(app, "one", first_spec)
    _mount_cached_spec(app, "two", second_spec)

    response = TestClient(app).get("/_meta/aggregate_openapi")

    assert response.status_code == 200
    aggregate = response.json()
    schemas = aggregate["components"]["schemas"]
    assert schemas["Shared_two_2"]["properties"]["children"]["items"]["anyOf"][0][
        "$ref"
    ] == "#/components/schemas/Nested_two_2"
    assert aggregate["paths"]["/two/tool"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]["$ref"] == "#/components/schemas/Shared_two_2"
    assert first_spec == first_original
    assert second_spec == second_original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "disable_path",
    [
        "/_meta/servers/one/disable",
        "/_meta/servers/one/tools/tool/disable",
    ],
    ids=["server", "tool"],
)
async def test_disable_invalidates_cached_aggregate(
    tmp_path,
    disable_path: str,
) -> None:
    app = await _build_app(tmp_path)
    spec = _tool_spec(
        title="One",
        schemas={"Shared": {"type": "object", "properties": {}}},
    )
    _mount_cached_spec(app, "one", spec)
    client = TestClient(app)

    first = client.get("/_meta/aggregate_openapi")
    assert first.status_code == 200
    assert "/one/tool" in first.json()["paths"]
    assert app.state.aggregate_openapi_dirty is False

    disabled = client.post(disable_path)
    assert disabled.status_code == 200
    assert app.state.aggregate_openapi_dirty is True

    refreshed = client.get("/_meta/aggregate_openapi")
    assert refreshed.status_code == 200
    assert "/one/tool" not in refreshed.json()["paths"]
    assert app.state.aggregate_openapi_dirty is False


@pytest.mark.asyncio
async def test_rest_toggle_hides_and_restores_aggregate_schema(tmp_path) -> None:
    app = await _build_app(tmp_path)
    spec = _tool_spec(
        title="One",
        schemas={"Shared": {"type": "object", "properties": {}}},
    )
    _mount_cached_spec(app, "one", spec)
    client = TestClient(app)

    visible = client.get("/_meta/aggregate_openapi")
    assert "/one/tool" in visible.json()["paths"]

    disabled = client.post("/_meta/rest-tools/disable")
    hidden = client.get("/_meta/aggregate_openapi")
    assert disabled.json() == {"ok": True, "enabled": False}
    assert hidden.json()["paths"] == {}
    assert hidden.json()["tags"] == []
    assert hidden.json()["components"]["schemas"] == {}

    enabled = client.post("/_meta/rest-tools/enable")
    restored = client.get("/_meta/aggregate_openapi")
    assert enabled.json() == {"ok": True, "enabled": True}
    assert "/one/tool" in restored.json()["paths"]
