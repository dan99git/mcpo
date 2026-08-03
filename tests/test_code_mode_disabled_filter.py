"""
Disabled-server/tool filtering for Code Mode tool discovery.

search_tools must not leak tool names, descriptions, or schemas of servers
or tools that are currently disabled. Execution was already blocked; these
tests pin down discovery-time filtering at serve time.
"""

import json

import pytest

import mcpo.services.state as state_mod
from mcpo.middleware.code_mode import CodeModeMCPMiddleware
from mcpo.services.code_mode import (
    build_catalog,
    filter_enabled_entries,
    search_catalog,
)

TOOLS_BY_SERVER = {
    "alpha": [
        {
            "name": "web_search",
            "description": "Search the web for results",
            "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
        },
        {
            "name": "fetch_page",
            "description": "Fetch a web page by URL",
            "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}},
        },
    ],
    "beta": [
        {
            "name": "get_time",
            "description": "Get the current time",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ],
}


@pytest.fixture
def state(tmp_path, monkeypatch):
    manager = state_mod.StateManager(str(tmp_path / "state.json"))
    monkeypatch.setattr(state_mod, "_global_state_manager", manager)
    return manager


@pytest.fixture
def catalog():
    return build_catalog(TOOLS_BY_SERVER)


def _result_tools(results):
    return {r["tool"] for r in results}


def test_search_catalog_excludes_disabled_server(state, catalog):
    state.set_server_enabled("alpha", False)

    results = search_catalog(catalog, "")
    assert _result_tools(results) == {"beta.get_time"}

    # Keyword query targeting the disabled server returns nothing from it
    results = search_catalog(catalog, "web search")
    assert _result_tools(results) == set()


def test_search_catalog_excludes_disabled_tool(state, catalog):
    state.set_tool_enabled("alpha", "web_search", False)

    results = search_catalog(catalog, "")
    assert _result_tools(results) == {"alpha.fetch_page", "beta.get_time"}


def test_reenable_restores_visibility(state, catalog):
    state.set_server_enabled("alpha", False)
    assert _result_tools(search_catalog(catalog, "")) == {"beta.get_time"}

    state.set_server_enabled("alpha", True)
    assert _result_tools(search_catalog(catalog, "")) == {
        "alpha.web_search",
        "alpha.fetch_page",
        "beta.get_time",
    }

    state.set_tool_enabled("alpha", "web_search", False)
    assert "alpha.web_search" not in _result_tools(search_catalog(catalog, ""))
    state.set_tool_enabled("alpha", "web_search", True)
    assert "alpha.web_search" in _result_tools(search_catalog(catalog, ""))


def test_filter_enabled_entries_defaults_to_enabled(state, catalog):
    # Servers/tools never toggled default to enabled
    assert len(filter_enabled_entries(catalog)) == len(catalog)


def test_disabled_entries_never_fill_empty_query_slots(state, catalog):
    # Filtering happens before the limit slice: a disabled entry at the
    # head of the catalog must not shadow an enabled one.
    state.set_server_enabled("alpha", False)
    results = search_catalog(catalog, "", limit=1)
    assert _result_tools(results) == {"beta.get_time"}


def test_management_pseudo_server_exempt_from_state_filter(state):
    # "mcpo" management tools are gated by the per-session opt-in flag in
    # chat.py, not by MCP server state. A stale/disabled "mcpo" state entry
    # must not hide them once opted in.
    mgmt_catalog = build_catalog(
        {
            "mcpo": [
                {
                    "name": "reload_config",
                    "description": "Reload MCPO configuration",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            ]
        }
    )
    state.set_server_enabled("mcpo", False)
    assert _result_tools(search_catalog(mgmt_catalog, "reload")) == {
        "mcpo.reload_config"
    }


def test_middleware_handle_search_tools_filters_disabled(state, catalog):
    middleware = CodeModeMCPMiddleware(app=None)
    middleware._catalog = catalog
    middleware._catalog_built = True

    state.set_server_enabled("alpha", False)

    result = middleware.handle_search_tools({"query": "", "limit": 50})
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert {r["tool"] for r in payload["tools"]} == {"beta.get_time"}
    assert payload["total_available"] == 1

    # Re-enable restores middleware-path visibility
    state.set_server_enabled("alpha", True)
    result = middleware.handle_search_tools({"query": "", "limit": 50})
    payload = json.loads(result["content"][0]["text"])
    assert {r["tool"] for r in payload["tools"]} == {
        "alpha.web_search",
        "alpha.fetch_page",
        "beta.get_time",
    }
    assert payload["total_available"] == 3
