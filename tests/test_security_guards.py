import base64
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.mark.asyncio
async def test_internal_management_routes_require_api_key():
    from mcpo.main import build_main_app

    app = await build_main_app(api_key="test-secret")
    client = TestClient(app)

    missing = client.get("/mcpo/get_config")
    assert missing.status_code == 401

    invalid = client.get(
        "/mcpo/get_config",
        headers={"Authorization": "Bearer wrong-secret"},
    )
    assert invalid.status_code == 403

    authenticated = client.get(
        "/mcpo/get_config",
        headers={"Authorization": "Bearer test-secret"},
    )
    assert authenticated.status_code == 400

    package_missing = client.get("/_meta/skill-packages")
    assert package_missing.status_code == 401

    package_authenticated = client.get(
        "/_meta/skill-packages",
        headers={"Authorization": "Bearer test-secret"},
    )
    assert package_authenticated.status_code == 200

    tool_preview_missing = client.post(
        "/_meta/tool-manifests/preview",
        json={"filename": "proposal.zip", "content_base64": "invalid"},
    )
    assert tool_preview_missing.status_code == 401

    tool_preview_authenticated = client.post(
        "/_meta/tool-manifests/preview",
        headers={"Authorization": "Bearer test-secret"},
        json={"filename": "proposal.zip", "content_base64": "invalid"},
    )
    assert tool_preview_authenticated.status_code == 422

    health_missing = client.get("/healthz")
    assert health_missing.status_code == 401

    health_authenticated = client.get(
        "/healthz",
        headers={"Authorization": "Bearer test-secret"},
    )
    assert health_authenticated.status_code == 200

    basic_credentials = base64.b64encode(b"user:test-secret").decode("ascii")
    health_basic = client.get(
        "/healthz",
        headers={"Authorization": f"Basic {basic_credentials}"},
    )
    assert health_basic.status_code == 200

    wrong_basic_credentials = base64.b64encode(b"user:wrong-secret").decode("ascii")
    health_wrong_basic = client.get(
        "/healthz",
        headers={"Authorization": f"Basic {wrong_basic_credentials}"},
    )
    assert health_wrong_basic.status_code == 403

    # Non-strict auth protects API operations but keeps API discovery public.
    assert client.get("/openapi.json").status_code == 200


@pytest.mark.asyncio
async def test_management_writes_are_blocked_in_read_only_mode(tmp_path, monkeypatch):
    from mcpo.main import build_main_app

    config_path = tmp_path / "mcpo.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "example": {
                        "command": "python",
                        "args": ["-c", "print('ok')"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    app = await build_main_app(config_path=str(config_path), read_only=True)
    client = TestClient(app)

    writes = [
        ("delete", "/_meta/servers/example", None),
        ("post", "/_meta/config/save", {"content": "{}"}),
        ("post", "/_meta/config/mcpServers/save", {"data": {}}),
        ("post", "/_meta/requirements/save", {"content": "example==1.0"}),
        ("post", "/_meta/code-mode", {"enabled": True}),
        (
            "post",
            "/_meta/skills",
            {
                "id": "test-skill",
                "title": "Test Skill",
                "description": "Test skill",
                "content": "Instructions.",
            },
        ),
        ("post", "/_meta/skills/test-skill/enable", None),
        (
            "post",
            "/_meta/cli-packages/install",
            {"kind": "python", "spec": "ruff==1.0.0", "confirmed": True},
        ),
        (
            "post",
            "/_meta/cli-packages/python-ruff-deadbeef0000/uninstall",
            {"confirmed": True},
        ),
        (
            "post",
            "/_meta/skill-packages/install",
            {"filename": "test.skill", "content_base64": "invalid", "confirmed": True},
        ),
        (
            "post",
            "/_meta/skill-packages/test-package/uninstall",
            {"confirmed": True},
        ),
        ("post", "/mcpo/install_python_package", {"package_name": "example==1.0"}),
        ("post", "/mcpo/post_config", {"config": {"mcpServers": {}}}),
        ("post", "/mcpo/post_env", {"env_vars": {"EXAMPLE": "value"}}),
        ("post", "/mcpo/validate_and_install", {}),
        ("post", "/mcpo/post_requirements", {"content": "example==1.0"}),
    ]

    with (
        patch("mcpo.main.subprocess.run") as run_process,
        patch("mcpo.api.routers.admin.get_state_manager") as get_admin_state,
    ):
        for method, path, payload in writes:
            response = client.request(method, path, json=payload)
            assert response.status_code == 403, f"{method.upper()} {path}: {response.text}"
            assert response.json()["error"]["code"] == "read_only"

        run_process.assert_not_called()
        get_admin_state.assert_not_called()

    assert json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"] == {
        "example": {
            "command": "python",
            "args": ["-c", "print('ok')"],
        }
    }
    assert not (tmp_path / "requirements.txt").exists()
    assert not (tmp_path / ".env").exists()
