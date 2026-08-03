from __future__ import annotations

import base64
import io
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mcpo.api.routers import admin
from mcpo.services.cli_packages import CliPackageInstallError, CliPackageManifestError


class _SkillState:
    def __init__(self) -> None:
        self.states: dict[str, dict[str, bool]] = {}

    def get_all_skill_states(self):
        return dict(self.states)

    def set_skill_enabled(self, skill_id: str, enabled: bool) -> None:
        self.states[skill_id] = {"enabled": enabled}


def _archive() -> str:
    buffer = io.BytesIO()
    document = (
        "---\n"
        "name: demo-skill\n"
        "description: Demonstrates package installation\n"
        "---\n"
        "# Demo Skill\n\n"
        "Follow the requested workflow.\n"
    )
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("demo/skills/demo-skill/SKILL.md", document)
        archive.writestr("demo/skills/demo-skill/references/readme.md", "Reference\n")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _tool_archive(content: str) -> str:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("proposal/TOOL.yaml", content)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    state = _SkillState()
    monkeypatch.setenv("MCPO_SKILLS_DIR", str(tmp_path / "skills"))
    monkeypatch.setenv("MCPO_CLI_PACKAGES_DIR", str(tmp_path / "cli-packages"))
    monkeypatch.setattr("mcpo.services.skills.get_state_manager", lambda: state)
    monkeypatch.setattr(admin, "get_state_manager", lambda: state)
    app = FastAPI()
    app.state.read_only_mode = False
    app.include_router(admin.router, prefix="/_meta")
    return TestClient(app)


def test_skill_package_api_inspect_install_list_and_uninstall(client: TestClient) -> None:
    payload = {"filename": "demo-pack.skill", "content_base64": _archive()}

    inspection = client.post("/_meta/skill-packages/inspect", json=payload)
    assert inspection.status_code == 200
    assert inspection.json()["inspection"]["skills"][0]["id"] == "demo-skill"

    confirmation = client.post("/_meta/skill-packages/install", json=payload)
    assert confirmation.status_code == 409
    assert confirmation.json()["error"]["code"] == "confirmation_required"

    installed = client.post(
        "/_meta/skill-packages/install",
        json={**payload, "confirmed": True},
    )
    assert installed.status_code == 200
    assert installed.json()["package"]["id"] == "demo-pack"

    packages = client.get("/_meta/skill-packages").json()["packages"]
    assert [package["id"] for package in packages] == ["demo-pack"]

    skill = client.get("/_meta/skills/demo-skill").json()["skill"]
    assert skill["sourceKind"] == "installed"
    assert skill["editable"] is False
    assert skill["resourceCount"] == 1
    assert skill["enabled"] is False

    confirmation = client.post(
        "/_meta/skill-packages/demo-pack/uninstall",
        json={"confirmed": False},
    )
    assert confirmation.status_code == 409
    assert confirmation.json()["error"]["code"] == "confirmation_required"

    removed = client.post(
        "/_meta/skill-packages/demo-pack/uninstall",
        json={"confirmed": True},
    )
    assert removed.status_code == 200
    assert removed.json() == {"ok": True, "id": "demo-pack", "uninstalled": True}
    assert client.get("/_meta/skill-packages").json()["packages"] == []


def test_skill_endpoints_report_metadata_and_missing_enable(client: TestClient) -> None:
    saved = client.post(
        "/_meta/skills",
        json={
            "id": "local-demo",
            "title": "Local Demo",
            "description": "Local skill",
            "content": "# Local Demo\n\nUse this skill.",
        },
    )
    assert saved.status_code == 200

    response = client.get("/_meta/skills")
    assert response.status_code == 200
    body = response.json()
    assert body["issues"] == []
    skill = body["skills"][0]
    assert skill["sourceKind"] == "local"
    assert skill["format"] == "canonical"
    assert skill["editable"] is True
    assert "content" not in skill

    disabled = client.post("/_meta/skills/Local Demo/disable")
    assert disabled.status_code == 200
    assert client.get("/_meta/skills/local-demo").json()["skill"]["enabled"] is False

    missing = client.post("/_meta/skills/not-installed/enable")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


def test_skill_package_mutations_respect_read_only(client: TestClient) -> None:
    client.app.state.read_only_mode = True
    payload = {
        "filename": "demo-pack.skill",
        "content_base64": _archive(),
        "confirmed": True,
    }

    install = client.post("/_meta/skill-packages/install", json=payload)
    assert install.status_code == 403
    assert install.json()["error"]["code"] == "read_only"

    uninstall = client.post(
        "/_meta/skill-packages/demo-pack/uninstall",
        json={"confirmed": True},
    )
    assert uninstall.status_code == 403
    assert uninstall.json()["error"]["code"] == "read_only"


def test_skill_package_list_surfaces_corrupt_state(
    client: TestClient,
    tmp_path,
) -> None:
    package_dir = tmp_path / "skills" / "installed" / "broken-package"
    package_dir.mkdir(parents=True)
    (package_dir / "mcpo-package.json").write_text("not json", encoding="utf-8")

    response = client.get("/_meta/skill-packages")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_skill_package_state"


def test_cli_package_api_plans_exact_specs_and_requires_confirmation(
    client: TestClient,
    monkeypatch,
) -> None:
    plan = client.post(
        "/_meta/cli-packages/plan",
        json={"kind": "python", "spec": "ruff==0.9.10", "allow_scripts": False},
    )
    assert plan.status_code == 200
    assert plan.json()["plan"]["isolation"] == "venv"
    assert plan.json()["plan"]["exists"] is False

    invalid = client.post(
        "/_meta/cli-packages/plan",
        json={"kind": "npm", "spec": "eslint@latest"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_cli_package"

    calls = []

    def fake_install(kind, spec, allow_scripts=False):
        calls.append((kind, spec, allow_scripts))
        return {"packageId": "npm-demo-deadbeef0000", "executables": []}

    monkeypatch.setattr(admin, "install_cli_package", fake_install)
    payload = {
        "kind": "npm",
        "spec": "demo@1.2.3",
        "allow_scripts": True,
    }
    confirmation = client.post("/_meta/cli-packages/install", json=payload)
    assert confirmation.status_code == 409
    assert confirmation.json()["error"]["code"] == "confirmation_required"
    assert calls == []

    installed = client.post(
        "/_meta/cli-packages/install",
        json={**payload, "confirmed": True},
    )
    assert installed.status_code == 200
    assert installed.json()["package"]["packageId"] == "npm-demo-deadbeef0000"
    assert calls == [("npm", "demo@1.2.3", True)]


def test_cli_package_api_returns_bounded_install_diagnostics(
    client: TestClient,
    monkeypatch,
) -> None:
    def fail_install(*args, **kwargs):
        raise CliPackageInstallError(
            "Installer exited with code 2",
            argv=["python", "-m", "pip"],
            returncode=2,
            stdout="output",
            stderr="failure",
        )

    monkeypatch.setattr(admin, "install_cli_package", fail_install)
    response = client.post(
        "/_meta/cli-packages/install",
        json={
            "kind": "python",
            "spec": "ruff==0.9.10",
            "confirmed": True,
        },
    )

    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "cli_install_failed"
    assert error["details"]["returnCode"] == 2
    assert error["details"]["stderr"] == "failure"


def test_cli_package_state_errors_do_not_leak_host_paths(
    client: TestClient,
    monkeypatch,
) -> None:
    leaked_path = r"C:\Users\example\cli-packages\record\manifest.json"

    def fail_list():
        raise CliPackageManifestError(f"Missing package manifest: {leaked_path}")

    monkeypatch.setattr(admin, "list_cli_packages", fail_list)
    listed = client.get("/_meta/cli-packages")
    assert listed.status_code == 409
    assert listed.json()["error"]["message"] == "CLI package state is invalid"
    assert leaked_path not in listed.text

    def fail_preview(*args, **kwargs):
        raise CliPackageManifestError(f"Missing package manifest: {leaked_path}")

    monkeypatch.setattr(admin, "preview_tool_manifest_archive", fail_preview)
    preview = client.post(
        "/_meta/tool-manifests/preview",
        json={"filename": "proposal.zip", "content_base64": "AAAA"},
    )
    assert preview.status_code == 409
    assert preview.json()["error"]["message"] == "CLI package state is invalid"
    assert leaked_path not in preview.text


def test_cli_package_mutations_respect_read_only(
    client: TestClient,
    monkeypatch,
) -> None:
    calls = []
    monkeypatch.setattr(admin, "install_cli_package", lambda *args, **kwargs: calls.append("install"))
    monkeypatch.setattr(admin, "uninstall_cli_package", lambda *args, **kwargs: calls.append("uninstall"))
    client.app.state.read_only_mode = True

    install = client.post(
        "/_meta/cli-packages/install",
        json={
            "kind": "python",
            "spec": "ruff==0.9.10",
            "confirmed": True,
        },
    )
    uninstall = client.post(
        "/_meta/cli-packages/python-ruff-deadbeef0000/uninstall",
        json={"confirmed": True},
    )

    assert install.status_code == 403
    assert uninstall.status_code == 403
    assert calls == []


def test_tool_manifest_preview_is_read_only_and_returns_diagnostics(
    client: TestClient,
) -> None:
    client.app.state.read_only_mode = True
    valid_manifest = """schemaVersion: 1
name: demo-tool
description: Preview a command.
runtime:
  packageId: python-demo-123456789abc
  executable: demo.exe
  argv: []
inputSchema:
  type: object
  properties: {}
  required: []
capabilities:
  network: false
  filesystem:
    read: []
    write: []
  environment: []
limits:
  timeoutSeconds: 30
  outputBytes: 65536
activation: disabled
"""
    response = client.post(
        "/_meta/tool-manifests/preview",
        json={
            "filename": "proposal.zip",
            "content_base64": _tool_archive(valid_manifest),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["preview"]["manifests"][0]["valid"] is True
    assert body["preview"]["manifests"][0]["executionEligible"] is False

    invalid_manifest = client.post(
        "/_meta/tool-manifests/preview",
        json={
            "filename": "proposal.zip",
            "content_base64": _tool_archive("schemaVersion: 1\nschemaVersion: 1\n"),
        },
    )
    assert invalid_manifest.status_code == 200
    assert invalid_manifest.json()["preview"]["manifests"][0]["valid"] is False

    invalid_archive = client.post(
        "/_meta/tool-manifests/preview",
        json={"filename": "proposal.zip", "content_base64": "invalid"},
    )
    assert invalid_archive.status_code == 422
    assert invalid_archive.json()["error"]["code"] == "invalid_tool_archive"
