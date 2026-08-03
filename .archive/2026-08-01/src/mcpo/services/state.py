"""
State management service for server and tool state persistence.
"""
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class StateLoadError(RuntimeError):
    """Persisted control state could not be loaded safely."""


class StateManager:
    """Manages server and tool states with thread-safe operations."""
    
    def __init__(self, state_file_path: str = "mcpo_state.json"):
        self.state_file_path = state_file_path
        self._server_states = {}  # {server_name: {"enabled": bool, "tools": {tool_name: bool}}}
        self._provider_states = {}  # {provider_id: {"enabled": bool}}
        self._model_states = {}  # {model_id: {"enabled": bool}}
        self._skill_states = {}  # {skill_id: {"enabled": bool}}
        self._favorite_models: List[str] = []  # List of starred model IDs
        self._code_mode: bool = False  # Code mode toggle
        self._rest_tools_enabled: bool = True  # Global REST-side (port 8000) tools toggle
        self._chat_settings: Dict[str, Any] = {}
        # Use a re-entrant lock because save_state is called from within other
        # lock-protected methods, and a standard Lock would deadlock.
        self._lock = threading.RLock()
        self._state_signature: Optional[Tuple[int, int, int]] = None
        self._has_valid_state = False
        self._state_load_error: Optional[str] = None
        self.load_state()

    def _current_state_signature(self) -> Optional[Tuple[int, int, int]]:
        try:
            stat = Path(self.state_file_path).stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size, getattr(stat, "st_ino", 0))
    
    @staticmethod
    def _validated_state_data(data: Any) -> Dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("state root must be a JSON object")

        mapping_fields = (
            "server_states",
            "provider_states",
            "model_states",
            "skill_states",
        )
        mappings: Dict[str, Dict[str, Any]] = {}
        for field_name in mapping_fields:
            value = data.get(field_name, {})
            if not isinstance(value, dict):
                raise ValueError(f"{field_name} must be a JSON object")
            for item_name, item_state in value.items():
                if not isinstance(item_name, str) or not isinstance(item_state, dict):
                    raise ValueError(f"{field_name} entries must be named JSON objects")
                enabled = item_state.get("enabled", True)
                if type(enabled) is not bool:
                    raise ValueError(f"{field_name}.{item_name}.enabled must be boolean")
                if field_name == "server_states":
                    tools = item_state.get("tools", {})
                    if not isinstance(tools, dict) or any(
                        not isinstance(name, str) or type(state) is not bool
                        for name, state in tools.items()
                    ):
                        raise ValueError(
                            f"{field_name}.{item_name}.tools must map names to booleans"
                        )
            mappings[field_name] = value

        favorite_models = data.get("favorite_models", [])
        if not isinstance(favorite_models, list) or any(
            not isinstance(model_id, str) for model_id in favorite_models
        ):
            raise ValueError("favorite_models must be a list of strings")

        code_mode = data.get("code_mode", False)
        rest_tools_enabled = data.get("rest_tools_enabled", True)
        if type(code_mode) is not bool or type(rest_tools_enabled) is not bool:
            raise ValueError("code_mode and rest_tools_enabled must be booleans")

        chat_settings = data.get("chat_settings", {})
        if not isinstance(chat_settings, dict):
            raise ValueError("chat_settings must be a JSON object")

        return {
            **mappings,
            "favorite_models": favorite_models,
            "code_mode": code_mode,
            "rest_tools_enabled": rest_tools_enabled,
            "chat_settings": chat_settings,
        }

    def _apply_state_data(self, data: Dict[str, Any]) -> None:
        self._server_states = data["server_states"]
        self._provider_states = data["provider_states"]
        self._model_states = data["model_states"]
        self._skill_states = data["skill_states"]
        self._favorite_models = data["favorite_models"]
        self._code_mode = data["code_mode"]
        self._rest_tools_enabled = data["rest_tools_enabled"]
        self._chat_settings = data["chat_settings"]

    def load_state(self) -> Dict[str, Any]:
        """Load control state, retaining the last valid policy on reload errors."""
        import logging

        logger = logging.getLogger(__name__)
        with self._lock:
            state_path = Path(self.state_file_path)
            if not state_path.exists():
                if self._has_valid_state:
                    self._state_load_error = "Persisted state file disappeared"
                    logger.error(
                        "%s; retaining last known good control state: %s",
                        self._state_load_error,
                        self.state_file_path,
                    )
                    self._state_signature = None
                    return dict(self._server_states)
                data = self._validated_state_data({})
                self._apply_state_data(data)
                self._has_valid_state = True
                self._state_load_error = None
                self._state_signature = None
                return dict(self._server_states)

            try:
                with state_path.open("r", encoding="utf-8") as state_file:
                    data = self._validated_state_data(json.load(state_file))
            except Exception as exc:
                self._state_load_error = f"Failed to load persisted state: {exc}"
                self._state_signature = self._current_state_signature()
                if not self._has_valid_state:
                    raise StateLoadError(self._state_load_error) from exc
                logger.error(
                    "%s; retaining last known good control state",
                    self._state_load_error,
                )
                return dict(self._server_states)

            self._apply_state_data(data)
            self._has_valid_state = True
            self._state_load_error = None
            self._state_signature = self._current_state_signature()
            return dict(self._server_states)

    def get_state_load_error(self) -> Optional[str]:
        """Return the latest persisted-state load error, if the service is degraded."""
        with self._lock:
            return self._state_load_error

    def save_state(self) -> None:
        """Save server and tool states to state file."""
        with self._lock:
            try:
                state_data = {
                    "server_states": self._server_states,
                    "provider_states": self._provider_states,
                    "model_states": self._model_states,
                    "skill_states": self._skill_states,
                    "favorite_models": self._favorite_models,
                    "code_mode": self._code_mode,
                    "rest_tools_enabled": self._rest_tools_enabled,
                    "chat_settings": self._chat_settings,
                }
                dir_path = os.path.dirname(os.path.abspath(self.state_file_path))
                fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix='.tmp')
                try:
                    with os.fdopen(fd, 'w') as f:
                        json.dump(state_data, f, indent=2)
                    os.replace(tmp_path, self.state_file_path)
                    self._state_signature = self._current_state_signature()
                except Exception:
                    os.unlink(tmp_path)
                    raise
            except Exception as e:
                # Log error but don't crash
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Failed to save state: {e}")

    def refresh_if_changed(self) -> bool:
        """Reload state only when another process changed the state file."""
        with self._lock:
            signature = self._current_state_signature()
            if signature == self._state_signature:
                return False
            self.load_state()
            return True
    
    def get_server_state(self, server_name: str) -> Dict[str, Any]:
        """Get state for a specific server."""
        with self._lock:
            return self._server_states.get(server_name, {
                "enabled": True,
                "tools": {}
            })
    
    def set_server_enabled(self, server_name: str, enabled: bool) -> None:
        """Set server enabled state."""
        with self._lock:
            if server_name not in self._server_states:
                self._server_states[server_name] = {"enabled": enabled, "tools": {}}
            else:
                self._server_states[server_name]["enabled"] = enabled
            self.save_state()
    
    def set_tool_enabled(self, server_name: str, tool_name: str, enabled: bool) -> None:
        """Set tool enabled state for a specific server."""
        with self._lock:
            if server_name not in self._server_states:
                self._server_states[server_name] = {"enabled": True, "tools": {}}
            self._server_states[server_name]["tools"][tool_name] = enabled
            self.save_state()
    
    def is_server_enabled(self, server_name: str) -> bool:
        """Check if server is enabled."""
        with self._lock:
            return self._server_states.get(server_name, {}).get("enabled", True)
    
    def is_tool_enabled(self, server_name: str, tool_name: str) -> bool:
        """Check if tool is enabled."""
        with self._lock:
            server_state = self._server_states.get(server_name, {})
            return server_state.get("tools", {}).get(tool_name, True)
    
    def get_all_states(self) -> Dict[str, Any]:
        """Get all server states."""
        with self._lock:
            return dict(self._server_states)

    # --- Providers ---
    def get_provider_state(self, provider_id: str) -> Dict[str, Any]:
        with self._lock:
            return self._provider_states.get(provider_id, {"enabled": True})

    def set_provider_enabled(self, provider_id: str, enabled: bool) -> None:
        with self._lock:
            self._provider_states[provider_id] = {"enabled": bool(enabled)}
            self.save_state()

    def is_provider_enabled(self, provider_id: str) -> bool:
        with self._lock:
            return self._provider_states.get(provider_id, {}).get("enabled", True)

    def get_all_provider_states(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._provider_states)

    # --- Models ---
    def is_model_enabled(self, model_id: str) -> bool:
        with self._lock:
            return self._model_states.get(model_id, {}).get("enabled", True)

    def set_model_enabled(self, model_id: str, enabled: bool) -> None:
        with self._lock:
            self._model_states[model_id] = {"enabled": bool(enabled)}
            self.save_state()

    def get_all_model_states(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._model_states)

    # --- Skills ---
    def get_skill_state(self, skill_id: str) -> Dict[str, Any]:
        with self._lock:
            return self._skill_states.get(skill_id, {"enabled": True})

    def set_skill_enabled(self, skill_id: str, enabled: bool) -> None:
        with self._lock:
            self._skill_states[skill_id] = {"enabled": bool(enabled)}
            self.save_state()

    def is_skill_enabled(self, skill_id: str) -> bool:
        with self._lock:
            return self._skill_states.get(skill_id, {}).get("enabled", True)

    def get_all_skill_states(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._skill_states)

    # --- Favorite Models ---
    def get_favorite_models(self) -> List[str]:
        """Get list of favorite model IDs."""
        with self._lock:
            return list(self._favorite_models)

    def set_favorite_models(self, model_ids: List[str]) -> None:
        """Set the entire list of favorite model IDs."""
        with self._lock:
            self._favorite_models = list(model_ids)
            self.save_state()

    def add_favorite_model(self, model_id: str) -> None:
        """Add a model to favorites."""
        with self._lock:
            if model_id not in self._favorite_models:
                self._favorite_models.append(model_id)
                self.save_state()

    def remove_favorite_model(self, model_id: str) -> None:
        """Remove a model from favorites."""
        with self._lock:
            if model_id in self._favorite_models:
                self._favorite_models.remove(model_id)
                self.save_state()

    def is_model_favorite(self, model_id: str) -> bool:
        """Check if a model is in favorites."""
        with self._lock:
            return model_id in self._favorite_models

    # --- Code Mode ---
    def is_code_mode_enabled(self) -> bool:
        """Check if code mode is enabled."""
        with self._lock:
            return self._code_mode

    def set_code_mode_enabled(self, enabled: bool) -> None:
        """Enable or disable code mode."""
        with self._lock:
            self._code_mode = bool(enabled)
            self.save_state()

    # --- REST Tools (global toggle for the REST/OpenAPI proxy side, port 8000) ---
    def is_rest_tools_enabled(self) -> bool:
        """Check if REST-side tool endpoints are globally enabled."""
        with self._lock:
            return self._rest_tools_enabled

    def set_rest_tools_enabled(self, enabled: bool) -> None:
        """Enable or disable REST-side tool endpoints globally."""
        with self._lock:
            self._rest_tools_enabled = bool(enabled)
            self.save_state()

    # --- Chat harness defaults ---
    def get_chat_settings(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._chat_settings)

    def set_chat_settings(self, settings: Dict[str, Any]) -> None:
        with self._lock:
            self._chat_settings = dict(settings)
            self.save_state()


# Global instance for backward compatibility
_global_state_manager = None
_global_state_lock = threading.Lock()

def get_state_manager(state_file_path: str = "mcpo_state.json") -> StateManager:
    """Get or create global state manager instance."""
    global _global_state_manager
    if _global_state_manager is None:
        with _global_state_lock:
            if _global_state_manager is None:
                _global_state_manager = StateManager(state_file_path)
    return _global_state_manager
