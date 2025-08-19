import json
import asyncio
import traceback
from typing import Any, Dict, ForwardRef, List, Optional, Type, Union
import logging
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from mcp import ClientSession, types
from mcp.types import (
    CallToolResult,
    PARSE_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    INVALID_PARAMS,
    INTERNAL_ERROR,
)

from mcp.shared.exceptions import McpError

from pydantic import Field, create_model
from pydantic.fields import FieldInfo

MCP_ERROR_TO_HTTP_STATUS = {
    PARSE_ERROR: 400,
    INVALID_REQUEST: 400,
    METHOD_NOT_FOUND: 404,
    INVALID_PARAMS: 422,
    INTERNAL_ERROR: 500,
}

logger = logging.getLogger(__name__)

def normalize_server_type(server_type: str) -> str:
    """Normalize server_type to a standard value."""
    if server_type in ["streamable_http", "streamablehttp", "streamable-http"]:
        return "streamable-http"
    return server_type

def process_tool_response(result: CallToolResult) -> list:
    """Universal response processor: returns a list of primitive/structured values.

    For now each MCP content item is flattened into a python value. Upstream classification
    into structured envelope performed later if flag enabled.
    """
    response: list = []
    for content in result.content:
        if isinstance(content, types.TextContent):
            value = content.text
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    pass
            response.append(value)
        elif isinstance(content, types.ImageContent):
            # Represent images as data URI string; future: separate type with metadata
            response.append({"_kind": "image", "mimeType": content.mimeType, "data": content.data})
        elif isinstance(content, types.EmbeddedResource):
            response.append({"_kind": "resource", "uri": getattr(content, "uri", None)})
        else:
            response.append(str(content))
    return response


def _classify_item(val: Any) -> Dict[str, Any]:
    if val is None:
        return {"type": "null", "value": None}
    if isinstance(val, dict) and "_kind" in val:
        kind = val.get("_kind")
        if kind == "image":
            return {"type": "image", "mimeType": val.get("mimeType"), "data": val.get("data")}
        if kind == "resource":
            return {"type": "resource", "uri": val.get("uri")}
    if isinstance(val, str):
        return {"type": "text", "value": val}
    if isinstance(val, (int, float, bool)):
        return {"type": "scalar", "value": val}
    if isinstance(val, list):
        return {"type": "list", "value": val}
    if isinstance(val, dict):
        return {"type": "object", "value": val}
    return {"type": "string", "value": str(val)}


def build_success(result_value: Any, items: list, structured: bool) -> Dict[str, Any]:
    if not structured:
        # Maintain legacy shape
        if len(items) == 1:
            return {"ok": True, "result": result_value}
        return {"ok": True, "result": result_value}
    classified = [_classify_item(v) for v in items]
    return {
        "ok": True,
        "result": result_value,
        "output": {"type": "collection", "items": classified},
    }


def build_error(message: str, status: int, code: Optional[str] = None, data: Any = None, structured: bool = False) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"ok": False, "error": {"message": message}}
    if code:
        payload["error"]["code"] = code
    if data is not None:
        payload["error"]["data"] = data
    if structured:
        payload["output"] = {"type": "collection", "items": []}
    return payload


def name_needs_alias(name: str) -> bool:
    """Check if a field name needs aliasing (if it starts with '_')."""
    return name.startswith("_")


def generate_alias_name(original_name: str, existing_names: set) -> str:
    """
    Generate an alias field name by stripping unwanted chars, and avoiding conflicts with existing names.

    Args:
        original_name: The original field name (should start with '_')
        existing_names: Set of existing names to avoid conflicts with

    Returns:
        An alias name that doesn't conflict with existing names
    """
    alias_name = original_name.lstrip("_")
    # Handle potential naming conflicts
    original_alias_name = alias_name
    suffix_counter = 1
    while alias_name in existing_names:
        alias_name = f"{original_alias_name}_{suffix_counter}"
        suffix_counter += 1
    return alias_name


def _process_schema_property(
    _model_cache: Dict[str, Type],
    prop_schema: Dict[str, Any],
    model_name_prefix: str,
    prop_name: str,
    is_required: bool,
    schema_defs: Optional[Dict] = None,
) -> tuple[Union[Type, List, ForwardRef, Any], FieldInfo]:
    """
    Recursively processes a schema property to determine its Python type hint
    and Pydantic Field definition.

    Returns:
        A tuple containing (python_type_hint, pydantic_field).
        The pydantic_field contains default value and description.
    """
    if "$ref" in prop_schema:
        ref = prop_schema["$ref"]
        if ref.startswith("#/properties/"):
            # Remove common prefix in pathes.
            prefix_path = model_name_prefix.split("_form_model_")[-1]
            ref_path = ref.split("#/properties/")[-1]
            # Translate $ref path to model_name_prefix style.
            ref_path = ref_path.replace("/properties/", "_model_")
            ref_path = ref_path.replace("/items", "_item")
            # If $ref path is a prefix substring of model_name_prefix path,
            # there exists a circular reference.
            # The loop should be broke with a return to avoid exception.
            if prefix_path.startswith(ref_path):
                # TODO: Find the exact type hint for the $ref.
                return Any, Field(default=None, description="")
        ref = ref.split("/")[-1]
        assert ref in schema_defs, "Custom field not found"
        prop_schema = schema_defs[ref]

    prop_type = prop_schema.get("type")
    prop_desc = prop_schema.get("description", "")

    default_value = ... if is_required else prop_schema.get("default", None)
    pydantic_field = Field(default=default_value, description=prop_desc)

    # Handle the case where prop_type is missing but 'anyOf' key exists
    # In this case, use data type from 'anyOf' to determine the type hint
    if "anyOf" in prop_schema:
        type_hints = []
        for i, schema_option in enumerate(prop_schema["anyOf"]):
            type_hint, _ = _process_schema_property(
                _model_cache,
                schema_option,
                f"{model_name_prefix}_{prop_name}",
                f"choice_{i}",
                False,
                schema_defs=schema_defs,
            )
            type_hints.append(type_hint)
        return Union[tuple(type_hints)], pydantic_field

    # Handle the case where prop_type is a list of types, e.g. ['string', 'number']
    if isinstance(prop_type, list):
        # Create a Union of all the types
        type_hints = []
        for type_option in prop_type:
            # Create a temporary schema with the single type and process it
            temp_schema = dict(prop_schema)
            temp_schema["type"] = type_option
            type_hint, _ = _process_schema_property(
                _model_cache, temp_schema, model_name_prefix, prop_name, False, schema_defs=schema_defs
            )
            type_hints.append(type_hint)

        # Return a Union of all possible types
        return Union[tuple(type_hints)], pydantic_field

    # Apply basic constraint metadata if present
    def _augment_field(field_obj: FieldInfo, schema: Dict[str, Any]):
        # Simple numeric / length constraints and enum exposure for OpenAPI fidelity
        constraints_map = [
            ("minimum", "ge"),
            ("maximum", "le"),
            ("minLength", "min_length"),
            ("maxLength", "max_length"),
        ]
        for src, tgt in constraints_map:
            if src in schema:
                setattr(field_obj, tgt, schema[src])  # type: ignore[attr-defined]
        # Expose enum via json_schema_extra for downstream schema generation
        if "enum" in schema and isinstance(schema["enum"], list):
            extra = getattr(field_obj, 'json_schema_extra', None) or {}
            extra['enum'] = schema['enum']
            setattr(field_obj, 'json_schema_extra', extra)
        return field_obj

    pydantic_field = _augment_field(pydantic_field, prop_schema)

    if prop_type == "object":
        nested_properties = prop_schema.get("properties", {})
        nested_required = prop_schema.get("required", [])
        nested_fields = {}

        nested_model_name = f"{model_name_prefix}_{prop_name}_model".replace(
            "__", "_"
        ).rstrip("_")

        if nested_model_name in _model_cache:
            return _model_cache[nested_model_name], pydantic_field

        for name, schema in nested_properties.items():
            is_nested_required = name in nested_required
            nested_type_hint, nested_pydantic_field = _process_schema_property(
                _model_cache,
                schema,
                nested_model_name,
                name,
                is_nested_required,
                schema_defs,
            )

            if name_needs_alias(name):
                other_names = set().union(
                    nested_properties, nested_fields, _model_cache
                )
                alias_name = generate_alias_name(name, other_names)
                aliased_field = Field(
                    default=nested_pydantic_field.default,
                    description=nested_pydantic_field.description,
                    alias=name,
                )
                nested_fields[alias_name] = (nested_type_hint, aliased_field)
            else:
                nested_fields[name] = (nested_type_hint, nested_pydantic_field)

        if not nested_fields:
            return Dict[str, Any], pydantic_field

        NestedModel = create_model(nested_model_name, **nested_fields)
        _model_cache[nested_model_name] = NestedModel

        return NestedModel, pydantic_field

    elif prop_type == "array":
        items_schema = prop_schema.get("items")
        if not items_schema:
            # Default to list of anything if items schema is missing
            return List[Any], pydantic_field

        # Recursively determine the type of items in the array
        item_type_hint, _ = _process_schema_property(
            _model_cache,
            items_schema,
            f"{model_name_prefix}_{prop_name}",
            "item",
            False,  # Items aren't required at this level,
            schema_defs,
        )
        list_type_hint = List[item_type_hint]
        return list_type_hint, pydantic_field

    elif prop_type == "string":
        return str, pydantic_field
    elif prop_type == "integer":
        return int, pydantic_field
    elif prop_type == "boolean":
        return bool, pydantic_field
    elif prop_type == "number":
        return float, pydantic_field
    elif prop_type == "null":
        return None, pydantic_field
    else:
        return Any, pydantic_field


def get_model_fields(form_model_name, properties, required_fields, schema_defs=None):
    model_fields = {}

    _model_cache: Dict[str, Type] = {}

    for param_name, param_schema in properties.items():
        is_required = param_name in required_fields
        python_type_hint, pydantic_field_info = _process_schema_property(
            _model_cache,
            param_schema,
            form_model_name,
            param_name,
            is_required,
            schema_defs,
        )

        # Handle parameter names with leading underscores (e.g., __top, __filter) which Pydantic v2 does not allow
        if name_needs_alias(param_name):
            other_names = set().union(properties, model_fields, _model_cache)
            alias_name = generate_alias_name(param_name, other_names)
            aliased_field = Field(
                default=pydantic_field_info.default,
                description=pydantic_field_info.description,
                alias=param_name,
            )
            # Use the generated type hint and Field info
            model_fields[alias_name] = (python_type_hint, aliased_field)
        else:
            model_fields[param_name] = (python_type_hint, pydantic_field_info)

    return model_fields


def get_tool_handler(
    session,
    endpoint_name,
    form_model_fields,
    response_model_fields=None,
):
    if form_model_fields:
        FormModel = create_model(f"{endpoint_name}_form_model", **form_model_fields)
        ResponseModel = (
            create_model(f"{endpoint_name}_response_model", **response_model_fields)
            if response_model_fields
            else Any
        )

        def make_endpoint_func(
            endpoint_name: str, FormModel, session: ClientSession
        ):  # Parameterized endpoint
            # Use broad return type to satisfy static analysis (dynamic model)
            async def tool(form_data, request: Request):  # type: ignore[no-untyped-def]
                args = form_data.model_dump(exclude_none=True, by_alias=True)
                logger.info(f"Calling endpoint: {endpoint_name}, with args: {args}")
                structured = getattr(request.app.state, "structured_output", False)
                try:
                    # Enforced enable/disable state
                    server_name = request.app.title
                    global_app = request.app  # sub-app
                    parent_app = getattr(global_app.state, 'parent_app', None)
                    # For now store enable flags at parent level; fallback to current
                    state_app = parent_app or request.app
                    server_enabled = getattr(state_app.state, 'server_enabled', {}).get(server_name, True)
                    tool_enabled = getattr(state_app.state, 'tool_enabled', {}).get(server_name, {}).get(endpoint_name, True)
                    if not server_enabled or not tool_enabled:
                        # Metrics: count disabled as error with code 'disabled'
                        metrics = getattr(state_app.state, 'metrics', None)
                        if metrics is not None:
                            metrics['tool_errors_total'] += 1
                            metrics['tool_errors_by_code']['disabled'] = metrics['tool_errors_by_code'].get('disabled', 0) + 1
                        return JSONResponse(status_code=403, content=build_error("Tool disabled", 403, code="disabled", structured=structured))
                    # Determine effective timeout
                    default_timeout = getattr(request.app.state, "tool_timeout", 30)
                    max_timeout = getattr(request.app.state, "tool_timeout_max", default_timeout)
                    override = request.headers.get("X-Tool-Timeout") or request.query_params.get("timeout")
                    effective_timeout = default_timeout
                    if override is not None:
                        try:
                            effective_timeout = int(override)
                        except ValueError:
                            return JSONResponse(status_code=400, content=build_error("Invalid timeout value", 400, code="invalid_timeout", structured=structured))
                    if effective_timeout <= 0 or effective_timeout > max_timeout:
                        return JSONResponse(status_code=400, content=build_error("Timeout out of allowed range", 400, code="invalid_timeout", data={"max": max_timeout}, structured=structured))

                    try:
                        result = await asyncio.wait_for(session.call_tool(endpoint_name, arguments=args), timeout=effective_timeout)
                        metrics = getattr(state_app.state, 'metrics', None)
                        if metrics is not None:
                            metrics['tool_calls_total'] += 1
                    except asyncio.TimeoutError:
                        metrics = getattr(state_app.state, 'metrics', None)
                        if metrics is not None:
                            metrics['tool_errors_total'] += 1
                            metrics['tool_errors_by_code']['timeout'] = metrics['tool_errors_by_code'].get('timeout', 0) + 1
                        return JSONResponse(status_code=504, content=build_error("Tool timed out", 504, code="timeout", data={"timeoutSeconds": effective_timeout}, structured=structured))

                    if result.isError:
                        error_message = "Unknown tool execution error"
                        if result.content and isinstance(result.content[0], types.TextContent):
                            error_message = result.content[0].text
                        metrics = getattr(state_app.state, 'metrics', None)
                        if metrics is not None:
                            metrics['tool_errors_total'] += 1
                            metrics['tool_errors_by_code']['tool_error'] = metrics['tool_errors_by_code'].get('tool_error', 0) + 1
                        # Unified envelope
                        return JSONResponse(
                            status_code=500,
                            content=build_error(error_message, 500, structured=structured),
                        )

                    response_items = process_tool_response(result)
                    final_response = response_items[0] if len(response_items) == 1 else response_items
                    return JSONResponse(content=build_success(final_response, response_items, structured))

                except McpError as e:
                    logger.info(
                        f"MCP Error calling {endpoint_name}: {traceback.format_exc()}"
                    )
                    status_code = MCP_ERROR_TO_HTTP_STATUS.get(e.error.code, 500)
                    return JSONResponse(
                        status_code=status_code,
                        content=build_error(
                            e.error.message,
                            status_code,
                            code=e.error.code,
                            data=e.error.data,
                            structured=structured,
                        ),
                    )
                except Exception as e:
                    logger.info(
                        f"Unexpected error calling {endpoint_name}: {traceback.format_exc()}"
                    )
                    return JSONResponse(
                        status_code=500,
                        content=build_error("Unexpected error", 500, data={"error": str(e)}, structured=structured),
                    )

            return tool

        tool_handler = make_endpoint_func(endpoint_name, FormModel, session)
    else:

        def make_endpoint_func_no_args(
            endpoint_name: str, session: ClientSession
        ):  # Parameterless endpoint
            async def tool(request: Request):  # No parameters
                logger.info(f"Calling endpoint: {endpoint_name}, with no args")
                structured = getattr(request.app.state, "structured_output", False)
                try:
                    server_name = request.app.title
                    global_app = request.app
                    parent_app = getattr(global_app.state, 'parent_app', None)
                    state_app = parent_app or request.app
                    server_enabled = getattr(state_app.state, 'server_enabled', {}).get(server_name, True)
                    tool_enabled = getattr(state_app.state, 'tool_enabled', {}).get(server_name, {}).get(endpoint_name, True)
                    if not server_enabled or not tool_enabled:
                        metrics = getattr(state_app.state, 'metrics', None)
                        if metrics is not None:
                            metrics['tool_errors_total'] += 1
                            metrics['tool_errors_by_code']['disabled'] = metrics['tool_errors_by_code'].get('disabled', 0) + 1
                        return JSONResponse(status_code=403, content=build_error("Tool disabled", 403, code="disabled", structured=structured))
                    default_timeout = getattr(request.app.state, "tool_timeout", 30)
                    max_timeout = getattr(request.app.state, "tool_timeout_max", default_timeout)
                    override = request.headers.get("X-Tool-Timeout") or request.query_params.get("timeout")
                    effective_timeout = default_timeout
                    if override is not None:
                        try:
                            effective_timeout = int(override)
                        except ValueError:
                            return JSONResponse(status_code=400, content=build_error("Invalid timeout value", 400, code="invalid_timeout", structured=structured))
                    if effective_timeout <= 0 or effective_timeout > max_timeout:
                        return JSONResponse(status_code=400, content=build_error("Timeout out of allowed range", 400, code="invalid_timeout", data={"max": max_timeout}, structured=structured))

                    try:
                        result = await asyncio.wait_for(session.call_tool(endpoint_name, arguments={}), timeout=effective_timeout)  # Empty dict
                        metrics = getattr(state_app.state, 'metrics', None)
                        if metrics is not None:
                            metrics['tool_calls_total'] += 1
                    except asyncio.TimeoutError:
                        metrics = getattr(state_app.state, 'metrics', None)
                        if metrics is not None:
                            metrics['tool_errors_total'] += 1
                            metrics['tool_errors_by_code']['timeout'] = metrics['tool_errors_by_code'].get('timeout', 0) + 1
                        return JSONResponse(status_code=504, content=build_error("Tool timed out", 504, code="timeout", data={"timeoutSeconds": effective_timeout}, structured=structured))

                    if result.isError:
                        error_message = "Unknown tool execution error"
                        if result.content and isinstance(result.content[0], types.TextContent):
                            error_message = result.content[0].text
                        metrics = getattr(state_app.state, 'metrics', None)
                        if metrics is not None:
                            metrics['tool_errors_total'] += 1
                            metrics['tool_errors_by_code']['tool_error'] = metrics['tool_errors_by_code'].get('tool_error', 0) + 1
                        return JSONResponse(
                            status_code=500,
                            content=build_error(error_message, 500, structured=structured),
                        )

                    response_items = process_tool_response(result)
                    final_response = response_items[0] if len(response_items) == 1 else response_items
                    return JSONResponse(content=build_success(final_response, response_items, structured))

                except McpError as e:
                    logger.info(
                        f"MCP Error calling {endpoint_name}: {traceback.format_exc()}"
                    )
                    status_code = MCP_ERROR_TO_HTTP_STATUS.get(e.error.code, 500)
                    metrics = getattr(state_app.state, 'metrics', None)
                    if metrics is not None:
                        metrics['tool_errors_total'] += 1
                        metrics['tool_errors_by_code'][e.error.code] = metrics['tool_errors_by_code'].get(e.error.code, 0) + 1
                    return JSONResponse(
                        status_code=status_code,
                        content=build_error(
                            e.error.message,
                            status_code,
                            code=e.error.code,
                            data=e.error.data,
                            structured=structured,
                        ),
                    )
                except Exception as e:
                    logger.info(
                        f"Unexpected error calling {endpoint_name}: {traceback.format_exc()}"
                    )
                    metrics = getattr(state_app.state, 'metrics', None)
                    if metrics is not None:
                        metrics['tool_errors_total'] += 1
                        metrics['tool_errors_by_code']['unexpected'] = metrics['tool_errors_by_code'].get('unexpected', 0) + 1
                    return JSONResponse(
                        status_code=500,
                        content=build_error("Unexpected error", 500, data={"error": str(e)}, structured=structured),
                    )

            return tool

        tool_handler = make_endpoint_func_no_args(endpoint_name, session)

    return tool_handler
