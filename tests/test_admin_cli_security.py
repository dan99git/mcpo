import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from mcpo import app as installed_cli
from mcpo.cli import cli as module_cli
from mcpo.cli.commands import ServerRunner


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_cli_inputs(tmp_path: Path) -> tuple[Path, Path]:
    config_path = tmp_path / "mcpo.json"
    config_path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text("MCPO_API_KEY=env-secret\n", encoding="utf-8")
    return config_path, env_path


def _invoke_admin_cli(
    entrypoint: str,
    config_path: Path,
    env_path: Path,
    *,
    cli_api_key: str | None = None,
):
    captured: dict[str, str | None] = {}
    common_args = ["--config", str(config_path), "--env-path", str(env_path)]
    if cli_api_key is not None:
        common_args.extend(["--api-key", cli_api_key])

    # Both CLI entrypoints intentionally load dotenv values into the process
    # environment. The real command then exits, but CliRunner stays in-process,
    # so restore the complete environment after each invocation.
    with patch.dict(os.environ, dict(os.environ), clear=True):
        if entrypoint == "installed":
            async def fake_run(*_args, **kwargs):
                captured["api_key"] = kwargs.get("api_key")

            with patch("mcpo.main.run", new=fake_run):
                result = CliRunner().invoke(installed_cli, common_args)
        else:
            async def fake_start_server(*_args, **kwargs):
                captured["api_key"] = kwargs.get("api_key")

            with patch.object(ServerRunner, "start_server", new=fake_start_server):
                result = CliRunner().invoke(module_cli, ["serve", *common_args])

    assert result.exit_code == 0, result.output
    return captured["api_key"]


@pytest.mark.parametrize("entrypoint", ["installed", "module"])
def test_admin_cli_loads_api_key_from_env_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
) -> None:
    monkeypatch.delenv("MCPO_API_KEY", raising=False)
    config_path, env_path = _write_cli_inputs(tmp_path)

    assert _invoke_admin_cli(entrypoint, config_path, env_path) == "env-secret"


@pytest.mark.parametrize("entrypoint", ["installed", "module"])
def test_admin_cli_explicit_api_key_overrides_loaded_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
) -> None:
    monkeypatch.delenv("MCPO_API_KEY", raising=False)
    config_path, env_path = _write_cli_inputs(tmp_path)

    assert (
        _invoke_admin_cli(
            entrypoint,
            config_path,
            env_path,
            cli_api_key="cli-secret",
        )
        == "cli-secret"
    )


def test_standard_compose_requires_key_and_authenticates_healthcheck() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["mcpo"]

    assert service["environment"] == [
        "MCPO_API_KEY=${MCPO_API_KEY:?Set MCPO_API_KEY}"
    ]
    assert service["healthcheck"]["test"] == [
        "CMD-SHELL",
        'curl -f -H "Authorization: Bearer $${MCPO_API_KEY}" http://localhost:8000/healthz',
    ]


@pytest.mark.parametrize("entrypoint", ["installed", "module"])
def test_admin_cli_keeps_auth_optional_when_environment_is_unset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
) -> None:
    monkeypatch.delenv("MCPO_API_KEY", raising=False)
    config_path, env_path = _write_cli_inputs(tmp_path)
    env_path.write_text("", encoding="utf-8")

    assert _invoke_admin_cli(entrypoint, config_path, env_path) is None
