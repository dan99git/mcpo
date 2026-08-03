"""
Configuration utilities for environment variable interpolation.
"""
from copy import deepcopy
import json
import os
import re
from typing import Dict, Any, Mapping, Optional


BOS_VENDOR_SITE_ID_ENV = "BOS_VENDOR_SITE_ID"


def interpolate_env_placeholders(config_env: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Replace ${VARNAME} in string values with os.environ values."""
    if not isinstance(config_env, dict):
        return {}

    pattern = re.compile(r"\$\{([A-Za-z0-9_]+)\}")

    result = dict(config_env)
    for k, v in list(result.items()):
        if isinstance(v, str):
            def repl(m):
                var = m.group(1)
                return os.environ.get(var, "")
            result[k] = pattern.sub(repl, v)
    return result


def interpolate_env_placeholders_in_config(config_obj: Dict[str, Any]) -> Dict[str, Any]:
    """Walk mcpServers entries and expand ${VAR} placeholders in a deep copy."""
    if not isinstance(config_obj, dict):
        return config_obj
    cfg = json.loads(json.dumps(config_obj))  # deep copy
    servers = cfg.get("mcpServers", {})
    for name, server in list(servers.items()):
        if not isinstance(server, dict):
            continue
        env = server.get("env")
        if isinstance(env, dict):
            servers[name]["env"] = interpolate_env_placeholders(env)
        headers = server.get("headers")
        if isinstance(headers, dict):
            servers[name]["headers"] = interpolate_env_placeholders(headers)
        url = server.get("url")
        if isinstance(url, str):
            servers[name]["url"] = interpolate_env_placeholders({"url": url})["url"]
    return cfg


def vendor_site_port_from_env(
    env: Optional[Mapping[str, str]] = None,
) -> Optional[int]:
    """Return a validated vendor site port, or None when this deployment is not site-scoped."""
    source = os.environ if env is None else env
    raw_site_id = source.get(BOS_VENDOR_SITE_ID_ENV)
    if raw_site_id is None:
        return None
    if not re.fullmatch(r"\d{5}", raw_site_id):
        raise ValueError(
            f"{BOS_VENDOR_SITE_ID_ENV} must be exactly five decimal digits "
            f"in range 10000..65535; got {raw_site_id!r}"
        )
    site_port = int(raw_site_id)
    if not 10000 <= site_port <= 65535:
        raise ValueError(
            f"{BOS_VENDOR_SITE_ID_ENV} must be exactly five decimal digits "
            f"in range 10000..65535; got {raw_site_id!r}"
        )
    return site_port


def normalize_config_shape(config_obj: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize accepted config shapes to include top-level mcpServers.

    Supports both:
    - {"mcpServers": {...}}
    - {"config": {"mcpServers": {...}}, ...}
    """
    if not isinstance(config_obj, dict):
        return config_obj

    if "mcpServers" in config_obj:
        return config_obj

    nested = config_obj.get("config")
    if isinstance(nested, dict) and isinstance(nested.get("mcpServers"), dict):
        normalized = dict(config_obj)
        normalized["mcpServers"] = nested["mcpServers"]
        return normalized

    return config_obj


def replace_mcp_servers_preserving_shape(
    config_obj: Dict[str, Any],
    mcp_servers: Dict[str, Any],
) -> Dict[str, Any]:
    """Replace the authoritative mcpServers section without changing its owner."""
    candidate = deepcopy(config_obj)
    if "mcpServers" in candidate:
        candidate["mcpServers"] = deepcopy(mcp_servers)
        return candidate

    nested = candidate.get("config")
    if isinstance(nested, dict) and "mcpServers" in nested:
        nested["mcpServers"] = deepcopy(mcp_servers)
        return candidate

    candidate["mcpServers"] = deepcopy(mcp_servers)
    return candidate
