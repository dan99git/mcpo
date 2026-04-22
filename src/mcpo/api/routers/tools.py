"""
Tools router for dynamic MCP tool endpoint creation.

Exposes `create_dynamic_endpoints(app, api_dependency=None)` — the function
each MCP sub-app uses to register one FastAPI endpoint per MCP tool exposed
by its upstream session.
"""
import logging
from fastapi import Depends, FastAPI

from mcpo.utils.main import get_model_fields, get_tool_handler

logger = logging.getLogger(__name__)


async def create_dynamic_endpoints(app: FastAPI, api_dependency=None) -> None:
    """Register one POST endpoint per MCP tool on the given FastAPI (sub-)app.

    - Pulls the tool list from `app.state.session` after initializing.
    - Honors `app.state.disabled_tools` (list[str]) to skip configured tools.
    - Scopes operation_id by `app.state.config_key` (or the app title) so
      multi-server deployments don't clash in the aggregate OpenAPI spec.
    """
    session = getattr(app.state, "session", None)
    if not session:
        raise ValueError("Session is not initialized in the app state.")

    result = await session.initialize()
    server_info = getattr(result, "serverInfo", None)
    if server_info:
        app.title = server_info.name or app.title
        app.description = (
            f"{server_info.name} MCP Server" if server_info.name else app.description
        )
        app.version = server_info.version or app.version

    instructions = getattr(result, "instructions", None)
    if instructions:
        app.description = instructions

    tools_result = await session.list_tools()
    tools = tools_result.tools

    disabled_tools = getattr(app.state, "disabled_tools", [])
    if disabled_tools:
        original_count = len(tools)
        tools = [tool for tool in tools if tool.name not in disabled_tools]
        filtered_count = original_count - len(tools)
        if filtered_count > 0:
            logger.info(
                f"Filtered out {filtered_count} tool(s) for server '{app.title}': {disabled_tools}"
            )

    server_key = getattr(app.state, "config_key", None) or (app.title or "server").replace(" ", "_")

    for tool in tools:
        endpoint_name = tool.name
        endpoint_description = tool.description

        inputSchema = tool.inputSchema
        outputSchema = getattr(tool, "outputSchema", None)

        form_model_fields = get_model_fields(
            f"{endpoint_name}_form_model",
            inputSchema.get("properties", {}),
            inputSchema.get("required", []),
            inputSchema.get("$defs", {}),
        )

        response_model_fields = None
        if outputSchema:
            response_model_fields = get_model_fields(
                f"{endpoint_name}_response_model",
                outputSchema.get("properties", {}),
                outputSchema.get("required", []),
                outputSchema.get("$defs", {}),
            )

        tool_handler = get_tool_handler(
            session,
            endpoint_name,
            form_model_fields,
            response_model_fields,
        )

        app.post(
            f"/{endpoint_name}",
            summary=endpoint_name.replace("_", " ").title(),
            description=endpoint_description,
            response_model_exclude_none=True,
            operation_id=f"{server_key}.{endpoint_name}",
            dependencies=[Depends(api_dependency)] if api_dependency else [],
        )(tool_handler)
