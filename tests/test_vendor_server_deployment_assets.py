from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = REPO_ROOT / "docker-compose.gateway-poc.yml"
POC_README_PATH = REPO_ROOT / "deploy" / "gateway-poc" / "README.md"
VENDOR_SERVER_DIR = REPO_ROOT / "deploy" / "vendor-server"
VENDOR_SERVER_README_PATH = VENDOR_SERVER_DIR / "README.md"
INITIALIZER_PATH = VENDOR_SERVER_DIR / "Initialize-VendorServerStorage.ps1"
VALIDATOR_PATH = VENDOR_SERVER_DIR / "Test-VendorServerStorage.ps1"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _powershell() -> str:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        pytest.skip("PowerShell is not installed")
    return executable


def _ps_quote(value: Path | str) -> str:
    return str(value).replace("'", "''")


def _run_powershell(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            _powershell(),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_compose_preserves_live_poc_identity_and_runtime_command() -> None:
    compose = yaml.safe_load(_read(COMPOSE_PATH))
    service = compose["services"]["gateway-mcpo"]

    assert compose["name"] == (
        "mcpo-site-${BOS_VENDOR_SITE_ID:?Set BOS_VENDOR_SITE_ID in .env.gateway-poc}"
    )
    assert service["image"] == "mcpo-gateway-poc:local"
    assert service["ports"] == [
        "127.0.0.1:${BOS_VENDOR_SITE_ID:?Set BOS_VENDOR_SITE_ID in .env.gateway-poc}:8351"
    ]
    assert service["volumes"] == [
        "gateway_poc_data:/data",
        "./deploy/gateway-poc/config.gateway-poc.json:/data/mcpo.json:ro",
    ]
    assert service["command"] == [
        "--config",
        "/data/mcpo.json",
        "--host",
        "0.0.0.0",
        "--port",
        "8351",
        "--oauth",
        "--public-url",
        "https://${BOS_VENDOR_SITE_ID:?Set BOS_VENDOR_SITE_ID in .env.gateway-poc}.${MCPO_GATEWAY_PUBLIC_DOMAIN:?Set MCPO_GATEWAY_PUBLIC_DOMAIN in .env.gateway-poc}",
        "--log-level",
        "info",
    ]
    assert set(service["environment"]) == {
        "BOS_VENDOR_SITE_ID",
        "VENDOR_PUBLIC_DOMAIN",
        "MCPO_API_KEY",
        "BOS_VENDOR_SITE_ACCESS_KEY",
    }
    assert set(compose["volumes"]) == {"gateway_poc_data"}


def test_compose_declares_product_site_labels_and_resource_bounds() -> None:
    compose = yaml.safe_load(_read(COMPOSE_PATH))
    service = compose["services"]["gateway-mcpo"]

    assert service["labels"] == {
        "ai.lighting.product-role": "vendor-connector",
        "ai.lighting.runtime-role": "site-connector-container",
        "ai.lighting.runtime-lineage": "mcpo",
        "ai.lighting.site-id": (
            "${BOS_VENDOR_SITE_ID:?Set BOS_VENDOR_SITE_ID in .env.gateway-poc}"
        ),
    }
    assert str(service["cpus"]) == "1.0"
    assert service["mem_limit"] == "512m"
    assert service["mem_reservation"] == "128m"
    assert service["pids_limit"] == 256


def test_poc_readme_states_role_and_recreation_boundary() -> None:
    text = _read(POC_README_PATH)

    assert text.startswith("# Vendor Connector proof of concept")
    assert "Site Connector Container" in text
    assert "built from the MCPO codebase" in text
    assert "not the onsite gateway tunnel client" in text
    assert "not active on the already running container" in text
    assert "controlled recreation and runtime reinspection" in text


def test_poc_readme_has_no_control_corruption_and_separate_identity_bullets() -> None:
    text = _read(POC_README_PATH)
    invalid = [char for char in text if ord(char) < 32 and char not in "\r\n\t"]

    assert invalid == []
    lines = text.splitlines()
    assert "- project: `mcpo-site-<BOS_VENDOR_SITE_ID>`" in lines
    assert "- service: `gateway-mcpo`" in lines
    assert "- image: `mcpo-gateway-poc:local`" in lines
    assert "- volume: `gateway_poc_data`" in lines


def test_vendor_server_readme_documents_layout_and_security_gaps() -> None:
    text = _read(VENDOR_SERVER_README_PATH)

    assert "S:\\BOS-Vendor" in text
    for name in (
        "releases",
        "compose",
        "config",
        "state",
        "logs",
        "backups",
        "rollback",
        "sites",
        "secrets",
    ):
        assert name in text
    assert "SiteId" in text
    assert "10000..65535" in text
    assert "does not create secret values" in text
    assert "Secret ACL enforcement" in text
    assert "backup policy remain unimplemented" in text
    assert "Initialize-VendorServerStorage.ps1" in text
    assert "Test-VendorServerStorage.ps1" in text
    assert "-WhatIf" in text


@pytest.mark.parametrize("script_path", [INITIALIZER_PATH, VALIDATOR_PATH])
def test_vendor_storage_scripts_parse_without_errors(script_path: Path) -> None:
    command = (
        "$tokens = $null; $errors = $null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{_ps_quote(script_path)}', "
        "[ref]$tokens, [ref]$errors) | Out-Null; "
        "if ($errors.Count -gt 0) { "
        "$errors | ForEach-Object { [Console]::Error.WriteLine($_.Message) }; exit 1 }"
    )

    result = _run_powershell(command)

    assert result.returncode == 0, result.stderr


def test_initializer_exposes_should_process_controls() -> None:
    command = (
        f"$command = Get-Command '{_ps_quote(INITIALIZER_PATH)}'; "
        "if (-not $command.Parameters.ContainsKey('WhatIf')) { exit 1 }; "
        "if (-not $command.Parameters.ContainsKey('Confirm')) { exit 2 }"
    )

    result = _run_powershell(command)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("script_path", "extra_args"),
    [(INITIALIZER_PATH, "-Confirm:$false"), (VALIDATOR_PATH, "")],
)
def test_storage_scripts_refuse_roots_outside_drive_s(
    script_path: Path, extra_args: str, tmp_path: Path
) -> None:
    forbidden_root = tmp_path / "BOS-Vendor"
    command = (
        "$ErrorActionPreference = 'Stop'; try { "
        f"& '{_ps_quote(script_path)}' -Root '{_ps_quote(forbidden_root)}' "
        f"-SiteId '44354' {extra_args} 2>$null | Out-Null; exit 90 "
        "} catch { "
        "if ($_.Exception.Message -notmatch 'drive S') { exit 91 }; exit 0 }"
    )

    result = _run_powershell(command)

    assert result.returncode == 0, result.stderr
    assert not forbidden_root.exists()


@pytest.mark.parametrize("script_path", [INITIALIZER_PATH, VALIDATOR_PATH])
@pytest.mark.parametrize("site_id", ["4435", "abcde", "09999", "65536"])
def test_storage_scripts_reject_invalid_site_ids(
    script_path: Path, site_id: str, tmp_path: Path
) -> None:
    command = (
        "$ErrorActionPreference = 'Stop'; try { "
        f"& '{_ps_quote(script_path)}' -Root '{_ps_quote(tmp_path)}' "
        f"-SiteId '{site_id}' 2>$null | Out-Null; exit 90 "
        "} catch { "
        "if ($_.Exception.Message -notmatch 'five decimal digits') { exit 91 }; exit 0 }"
    )

    result = _run_powershell(command)

    assert result.returncode == 0, result.stderr


def test_validator_contains_no_filesystem_write_commands() -> None:
    text = _read(VALIDATOR_PATH).lower()
    forbidden_commands = (
        "new-item",
        "set-content",
        "add-content",
        "out-file",
        "copy-item",
        "move-item",
        "remove-item",
    )

    assert all(command not in text for command in forbidden_commands)


def test_initializer_creates_directories_only() -> None:
    text = _read(INITIALIZER_PATH).lower()
    forbidden_commands = (
        "set-content",
        "add-content",
        "out-file",
        "copy-item",
        "move-item",
    )

    assert all(command not in text for command in forbidden_commands)
    assert "new-item -itemtype directory" in text


def test_initializer_is_idempotent_on_a_process_local_s_drive(tmp_path: Path) -> None:
    backing_root = tmp_path / "s-drive"
    backing_root.mkdir()
    command = (
        "$ErrorActionPreference = 'Stop'; & { "
        f"New-PSDrive -Name S -PSProvider FileSystem -Root '{_ps_quote(backing_root)}' "
        "-Scope Local -ErrorAction Stop | Out-Null; "
        f"& '{_ps_quote(INITIALIZER_PATH)}' -Root 'S:\\BOS-Vendor' "
        "-SiteId '44354' -Confirm:$false | Out-Null; "
        f"& '{_ps_quote(INITIALIZER_PATH)}' -Root 'S:\\BOS-Vendor' "
        "-SiteId '44354' -Confirm:$false | Out-Null; "
        f"& '{_ps_quote(VALIDATOR_PATH)}' -Root 'S:\\BOS-Vendor' "
        "-SiteId '44354' | Out-Null }"
    )

    result = _run_powershell(command)

    assert result.returncode == 0, result.stderr
    root = backing_root / "BOS-Vendor"
    assert {path.name for path in root.iterdir()} == {
        "releases",
        "compose",
        "config",
        "state",
        "logs",
        "backups",
        "rollback",
        "sites",
    }
    site_root = root / "sites" / "44354"
    assert {path.name for path in site_root.iterdir()} == {
        "config",
        "state",
        "logs",
        "backups",
        "rollback",
        "secrets",
    }
    assert list((site_root / "secrets").iterdir()) == []
    assert all(path.is_dir() for path in root.rglob("*"))
