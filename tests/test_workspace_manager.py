"""Tests for tools/workspace-manager/workspace_manager.py.

Exercises the pure validation/rewrite logic directly (no server spawn).
The module lives under a hyphenated directory name (tools/workspace-manager)
so it is loaded via importlib from its file path rather than a normal
package import.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "tools"
    / "workspace-manager"
    / "workspace_manager.py"
)

_spec = importlib.util.spec_from_file_location("workspace_manager", MODULE_PATH)
wm = importlib.util.module_from_spec(_spec)
sys.modules["workspace_manager"] = wm
_spec.loader.exec_module(wm)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def make_config(allowlist, targets, active="D:/old", nested=False, extra_servers=None):
    """Build a minimal config dict in either nested-"config" or top-level
    mcpServers shape."""
    servers = {
        "workspaceFiles": {
            "command": "npx",
            "args": ["-y", "@some/server", "D:/old"],
        },
        "otherEnvServer": {
            "command": "python",
            "args": ["server.py"],
        },
    }
    if extra_servers:
        servers.update(extra_servers)

    workspace = {"active": active, "allowlist": allowlist, "targets": targets}

    if nested:
        return {"server": "mcpo", "config": {"mcpServers": servers}, "workspace": workspace}
    return {"mcpServers": servers, "workspace": workspace}


# ---------------------------------------------------------------------------
# workspace section validation
# ---------------------------------------------------------------------------

def test_missing_workspace_section_error():
    config = {"mcpServers": {}}
    with pytest.raises(wm.WorkspaceError, match="workspace"):
        wm.validate_and_get_workspace(config)


def test_empty_allowlist_error():
    config = {"mcpServers": {}, "workspace": {"allowlist": [], "targets": {}}}
    with pytest.raises(wm.WorkspaceError, match="allowlist"):
        wm.validate_and_get_workspace(config)


def test_missing_targets_error():
    config = {"mcpServers": {}, "workspace": {"allowlist": ["D:/foo"]}}
    with pytest.raises(wm.WorkspaceError, match="targets"):
        wm.validate_and_get_workspace(config)


# ---------------------------------------------------------------------------
# path validation
# ---------------------------------------------------------------------------

def test_relative_path_rejected(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    with pytest.raises(wm.WorkspaceError, match="absolute"):
        wm.validate_new_path("relative/sub", [str(allowed)])


def test_nonexistent_path_rejected(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    missing = allowed / "does_not_exist"
    with pytest.raises(wm.WorkspaceError, match="does not exist"):
        wm.validate_new_path(str(missing), [str(allowed)])


def test_file_not_dir_rejected(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    a_file = allowed / "file.txt"
    a_file.write_text("hi")
    with pytest.raises(wm.WorkspaceError, match="not a directory"):
        wm.validate_new_path(str(a_file), [str(allowed)])


def test_path_outside_allowlist_rejected(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(wm.WorkspaceError, match="not inside"):
        wm.validate_new_path(str(outside), [str(allowed)])


def test_dotdot_traversal_rejected(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    # allowed/../outside resolves (via realpath) to tmp_path/outside, which
    # is not inside allowed.
    traversal = str(allowed / ".." / "outside")
    with pytest.raises(wm.WorkspaceError, match="not inside"):
        wm.validate_new_path(traversal, [str(allowed)])


def test_sibling_prefix_trick_rejected(tmp_path):
    """allowlist tmp_path/foo must not match tmp_path/foobar via naive
    string prefix matching."""
    foo = tmp_path / "foo"
    foo.mkdir()
    foobar = tmp_path / "foobar"
    foobar.mkdir()
    with pytest.raises(wm.WorkspaceError, match="not inside"):
        wm.validate_new_path(str(foobar), [str(foo)])


def test_path_equal_to_allowlist_entry_accepted(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    resolved = wm.validate_new_path(str(allowed), [str(allowed)])
    assert resolved == Path(os.path.realpath(allowed))


def test_path_nested_inside_allowlist_entry_accepted(tmp_path):
    allowed = tmp_path / "allowed"
    sub = allowed / "sub" / "deeper"
    sub.mkdir(parents=True)
    resolved = wm.validate_new_path(str(sub), [str(allowed)])
    assert resolved == Path(os.path.realpath(sub))


# ---------------------------------------------------------------------------
# happy path: arg rewrite
# ---------------------------------------------------------------------------

def test_happy_path_arg_rewrite(tmp_path):
    allowed = tmp_path / "allowed"
    newroot = allowed / "project"
    newroot.mkdir(parents=True)

    config_path = tmp_path / "mcpo.json"
    targets = {"workspaceFiles": {"kind": "arg", "position": "last"}}
    config = make_config([str(allowed)], targets)
    write_json(config_path, config)

    result = wm.set_workspace_path(config_path, str(newroot))

    assert result["updated_servers"] == ["workspaceFiles"]
    assert result["active"] == wm.normalize_path_for_config(str(Path(os.path.realpath(newroot))))

    written = json.loads(config_path.read_text(encoding="utf-8"))
    args = written["mcpServers"]["workspaceFiles"]["args"]
    # other args preserved, only the last one replaced
    assert args[0] == "-y"
    assert args[1] == "@some/server"
    assert args[2] == result["active"]
    assert written["workspace"]["active"] == result["active"]


# ---------------------------------------------------------------------------
# happy path: env rewrite
# ---------------------------------------------------------------------------

def test_happy_path_env_rewrite_creates_env_if_absent(tmp_path):
    allowed = tmp_path / "allowed"
    newroot = allowed / "project"
    newroot.mkdir(parents=True)

    config_path = tmp_path / "mcpo.json"
    targets = {"otherEnvServer": {"kind": "env", "name": "WORKSPACE_ROOT"}}
    config = make_config([str(allowed)], targets)
    write_json(config_path, config)

    assert "env" not in config["mcpServers"]["otherEnvServer"]

    result = wm.set_workspace_path(config_path, str(newroot))

    written = json.loads(config_path.read_text(encoding="utf-8"))
    env = written["mcpServers"]["otherEnvServer"]["env"]
    assert env["WORKSPACE_ROOT"] == result["active"]


def test_happy_path_env_rewrite_preserves_existing_env_keys(tmp_path):
    allowed = tmp_path / "allowed"
    newroot = allowed / "project"
    newroot.mkdir(parents=True)

    config_path = tmp_path / "mcpo.json"
    targets = {"otherEnvServer": {"kind": "env", "name": "WORKSPACE_ROOT"}}
    config = make_config(
        [str(allowed)],
        targets,
        extra_servers={
            "otherEnvServer": {
                "command": "python",
                "args": ["server.py"],
                "env": {"OTHER_VAR": "keep-me"},
            }
        },
    )
    write_json(config_path, config)

    result = wm.set_workspace_path(config_path, str(newroot))

    written = json.loads(config_path.read_text(encoding="utf-8"))
    env = written["mcpServers"]["otherEnvServer"]["env"]
    assert env["OTHER_VAR"] == "keep-me"
    assert env["WORKSPACE_ROOT"] == result["active"]


# ---------------------------------------------------------------------------
# shape preservation
# ---------------------------------------------------------------------------

def test_nested_config_shape_preserved_on_write(tmp_path):
    allowed = tmp_path / "allowed"
    newroot = allowed / "project"
    newroot.mkdir(parents=True)

    config_path = tmp_path / "mcpo.json"
    targets = {"workspaceFiles": {"kind": "arg", "position": "last"}}
    config = make_config([str(allowed)], targets, nested=True)
    write_json(config_path, config)

    wm.set_workspace_path(config_path, str(newroot))

    written = json.loads(config_path.read_text(encoding="utf-8"))
    assert "config" in written
    assert "mcpServers" in written["config"]
    assert "mcpServers" not in written  # top-level shape not introduced
    assert written["server"] == "mcpo"


def test_top_level_shape_preserved_on_write(tmp_path):
    allowed = tmp_path / "allowed"
    newroot = allowed / "project"
    newroot.mkdir(parents=True)

    config_path = tmp_path / "mcpo.json"
    targets = {"workspaceFiles": {"kind": "arg", "position": "last"}}
    config = make_config([str(allowed)], targets, nested=False)
    write_json(config_path, config)

    wm.set_workspace_path(config_path, str(newroot))

    written = json.loads(config_path.read_text(encoding="utf-8"))
    assert "mcpServers" in written
    assert "config" not in written


# ---------------------------------------------------------------------------
# unknown target aborts, no file change
# ---------------------------------------------------------------------------

def test_unknown_target_server_aborts_with_no_file_change(tmp_path):
    allowed = tmp_path / "allowed"
    newroot = allowed / "project"
    newroot.mkdir(parents=True)

    config_path = tmp_path / "mcpo.json"
    targets = {
        "workspaceFiles": {"kind": "arg", "position": "last"},
        "doesNotExistServer": {"kind": "arg", "position": "last"},
    }
    config = make_config([str(allowed)], targets)
    write_json(config_path, config)
    original_bytes = config_path.read_bytes()

    with pytest.raises(wm.WorkspaceError, match="doesNotExistServer"):
        wm.set_workspace_path(config_path, str(newroot))

    assert config_path.read_bytes() == original_bytes


def test_unknown_kind_aborts_with_no_file_change(tmp_path):
    allowed = tmp_path / "allowed"
    newroot = allowed / "project"
    newroot.mkdir(parents=True)

    config_path = tmp_path / "mcpo.json"
    targets = {"workspaceFiles": {"kind": "bogus"}}
    config = make_config([str(allowed)], targets)
    write_json(config_path, config)
    original_bytes = config_path.read_bytes()

    with pytest.raises(wm.WorkspaceError, match="unknown kind"):
        wm.set_workspace_path(config_path, str(newroot))

    assert config_path.read_bytes() == original_bytes


# ---------------------------------------------------------------------------
# atomic write
# ---------------------------------------------------------------------------

def test_atomic_write_leaves_valid_json(tmp_path):
    allowed = tmp_path / "allowed"
    newroot = allowed / "project"
    newroot.mkdir(parents=True)

    config_path = tmp_path / "mcpo.json"
    targets = {"workspaceFiles": {"kind": "arg", "position": "last"}}
    config = make_config([str(allowed)], targets)
    write_json(config_path, config)

    wm.set_workspace_path(config_path, str(newroot))

    # File is parseable and no stray .tmp files were left behind.
    json.loads(config_path.read_text(encoding="utf-8"))
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_write_config_atomic_uses_temp_file_and_replace(tmp_path):
    config_path = tmp_path / "mcpo.json"
    write_json(config_path, {"a": 1})

    wm.write_config_atomic(config_path, {"a": 2, "b": [1, 2, 3]})

    written = json.loads(config_path.read_text(encoding="utf-8"))
    assert written == {"a": 2, "b": [1, 2, 3]}
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


# ---------------------------------------------------------------------------
# get_workspace_info
# ---------------------------------------------------------------------------

def test_get_workspace_info_returns_active_allowlist_targets(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()

    config_path = tmp_path / "mcpo.json"
    targets = {"workspaceFiles": {"kind": "arg", "position": "last"}}
    config = make_config([str(allowed)], targets, active="D:/current")
    write_json(config_path, config)

    info = wm.get_workspace_info(config_path)

    assert info["active"] == "D:/current"
    assert info["allowlist"] == [str(allowed)]
    assert info["targets"] == targets
