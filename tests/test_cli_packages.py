import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from mcpo.services import cli_packages
from mcpo.services.cli_packages import (
    CliPackageCollisionError,
    CliPackageInstallError,
    CliPackageManifestError,
    CliPackageSafetyError,
    CliPackageValidationError,
    install_cli_package,
    list_cli_packages,
    plan_cli_package,
    uninstall_cli_package,
)


@pytest.fixture
def package_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "cli package root"
    monkeypatch.setenv("MCPO_CLI_PACKAGES_DIR", str(root))
    return root


def _make_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    path.chmod(0o755)


def _mock_install_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    stdout: str = "installed",
) -> tuple[list[tuple[list[str], dict]], Path]:
    calls: list[tuple[list[str], dict]] = []
    npm_executable = tmp_path / "fake-bin" / ("npm.cmd" if os.name == "nt" else "npm")
    _make_executable(npm_executable)
    monkeypatch.setattr(
        cli_packages.shutil,
        "which",
        lambda name: str(npm_executable) if name == "npm" else None,
    )

    def fake_run(argv, **kwargs):
        command = [str(item) for item in argv]
        calls.append((command, kwargs))
        if command[1:3] == ["-m", "venv"]:
            payload_dir = Path(command[-1])
            venv_python = cli_packages._venv_python(payload_dir)
            _make_executable(venv_python)
        elif command[1:4] == ["-m", "pip", "install"]:
            _make_executable(Path(command[0]).parent / "demo-cli")
        elif command[0] == str(npm_executable.resolve()):
            prefix = Path(command[command.index("--prefix") + 1])
            _make_executable(prefix / "node_modules" / ".bin" / "demo-cli")
        kwargs["stdout"].write(stdout.encode("utf-8"))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(cli_packages.subprocess, "run", fake_run)
    return calls, npm_executable.resolve()


def _build_smoke_wheel(wheelhouse: Path) -> Path:
    wheelhouse.mkdir(parents=True)
    wheel_path = wheelhouse / "mcpo_cli_smoke-0.0.1-py3-none-any.whl"
    files = {
        "smoke_cli/__init__.py": "",
        "smoke_cli/cli.py": (
            "def main():\n"
            "    print('mcpo-cli-smoke-ok')\n"
        ),
        "mcpo_cli_smoke-0.0.1.dist-info/METADATA": (
            "Metadata-Version: 2.1\n"
            "Name: mcpo-cli-smoke\n"
            "Version: 0.0.1\n"
        ),
        "mcpo_cli_smoke-0.0.1.dist-info/WHEEL": (
            "Wheel-Version: 1.0\n"
            "Generator: mcpo-test\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n"
        ),
        "mcpo_cli_smoke-0.0.1.dist-info/entry_points.txt": (
            "[console_scripts]\n"
            "mcpo-cli-smoke = smoke_cli.cli:main\n"
        ),
    }
    record_path = "mcpo_cli_smoke-0.0.1.dist-info/RECORD"
    record = "".join(f"{name},,\n" for name in files) + f"{record_path},,\n"
    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr(record_path, record)
    return wheel_path


@pytest.mark.parametrize(
    ("kind", "spec"),
    [
        ("python", "ruff"),
        ("python", "ruff>=0.9.0"),
        ("python", "ruff==0.9.*"),
        ("python", "git+https://example.test/repo.git"),
        ("python", "../ruff==0.9.0"),
        ("python", "-e ruff==0.9.0"),
        ("python", "ruff==1.0.0;python_version>'3.11'"),
        ("npm", "eslint"),
        ("npm", "eslint@latest"),
        ("npm", "eslint@^9.0.0"),
        ("npm", "eslint@9.0"),
        ("npm", "../eslint@9.0.0"),
        ("npm", "https://example.test/package.tgz"),
        ("npm", "--global"),
    ],
)
def test_plan_rejects_unsafe_or_unpinned_specs(kind: str, spec: str) -> None:
    with pytest.raises(CliPackageValidationError):
        plan_cli_package(kind, spec)


def test_plan_accepts_only_supported_exact_specs(package_root: Path) -> None:
    python_plan = plan_cli_package("python", "Black[Jupyter,colorama]==24.10.0")
    npm_plan = plan_cli_package("npm", "@scope/tool@1.2.3")
    npm_prerelease = plan_cli_package("npm", "eslint@9.0.0-beta.1+build.2")

    assert python_plan["kind"] == "python"
    assert python_plan["name"] == "black"
    assert python_plan["version"] == "24.10.0"
    assert python_plan["isolation"] == "venv"
    assert npm_plan["name"] == "@scope/tool"
    assert npm_plan["version"] == "1.2.3"
    assert npm_prerelease["version"] == "9.0.0-beta.1+build.2"
    assert npm_plan["isolation"] == "npm-prefix"
    assert Path(python_plan["root"]) == package_root.resolve()
    assert not package_root.exists()


@pytest.mark.parametrize(
    ("kind", "spec", "allow_scripts"),
    [
        ("pip", "ruff==1.0.0", False),
        ("Python", "ruff==1.0.0", False),
        ("python", "ruff==1.0.0", 1),
        ("npm", "eslint@1.2.3", "false"),
    ],
)
def test_plan_rejects_invalid_kind_and_script_flag(kind, spec, allow_scripts) -> None:
    with pytest.raises(CliPackageValidationError):
        plan_cli_package(kind, spec, allow_scripts=allow_scripts)


def test_python_install_uses_stable_isolated_venv_and_manifest(
    package_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls, _ = _mock_install_commands(monkeypatch, tmp_path)

    installed = install_cli_package("python", "ruff==0.9.10")

    assert len(calls) == 2
    venv_command, venv_kwargs = calls[0]
    pip_command, pip_kwargs = calls[1]
    assert venv_command == [
        str(Path(sys.executable).resolve()),
        "-m",
        "venv",
        installed["installDir"],
    ]
    assert pip_command == [
        str(cli_packages._venv_python(Path(installed["installDir"]))),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--only-binary=:all:",
        "ruff==0.9.10",
    ]
    for kwargs in (venv_kwargs, pip_kwargs):
        assert kwargs["shell"] is False
        assert kwargs["timeout"] == cli_packages.INSTALL_TIMEOUT_SECONDS
        assert "capture_output" not in kwargs
        assert hasattr(kwargs["stdout"], "write")
        assert hasattr(kwargs["stderr"], "write")

    assert Path(installed["installDir"]).name.startswith(".payload-python-ruff-")
    assert Path(installed["recordDir"]).is_dir()
    assert Path(installed["manifestPath"]).is_file()
    assert any(item["name"] == "demo-cli" for item in installed["executables"])
    assert not any(item["name"] == Path(sys.executable).name for item in installed["executables"])

    listed = list_cli_packages()
    assert len(listed) == 1
    assert listed[0]["packageId"] == installed["packageId"]
    assert listed[0]["installDir"] == installed["installDir"]
    assert "commands" not in listed[0]
    assert not any(path.name.startswith(".record-") for path in package_root.iterdir())


@pytest.mark.parametrize("allow_scripts", [False, True])
def test_npm_install_uses_one_prefix_and_safe_script_default(
    package_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    allow_scripts: bool,
) -> None:
    calls, npm_executable = _mock_install_commands(monkeypatch, tmp_path)

    installed = install_cli_package(
        "npm", "@scope/tool@1.2.3", allow_scripts=allow_scripts
    )

    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[:2] == [str(npm_executable), "install"]
    assert command[command.index("--prefix") + 1] == installed["installDir"]
    assert command.count("--prefix") == 1
    assert "--no-audit" in command
    assert "--no-fund" in command
    assert ("--ignore-scripts" in command) is (not allow_scripts)
    assert command[-1] == "@scope/tool@1.2.3"
    assert kwargs["shell"] is False
    assert any(item["name"] == "demo-cli" for item in installed["executables"])


def test_python_install_real_offline_wheel_roundtrip(
    package_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    _build_smoke_wheel(wheelhouse)
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("PIP_FIND_LINKS", str(wheelhouse))
    monkeypatch.setenv("PIP_DISABLE_PIP_VERSION_CHECK", "1")

    installed = install_cli_package("python", "mcpo-cli-smoke==0.0.1")

    executable = next(
        item
        for item in installed["executables"]
        if item["name"].lower().startswith("mcpo-cli-smoke")
    )
    result = subprocess.run(
        [executable["path"]],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        shell=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "mcpo-cli-smoke-ok"

    removed = uninstall_cli_package(installed["packageId"])
    assert removed["removed"] is True
    assert list_cli_packages() == []
    assert list(package_root.iterdir()) == []


def test_install_refuses_existing_package_without_running_installer(
    package_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls, _ = _mock_install_commands(monkeypatch, tmp_path)
    installed = install_cli_package("python", "ruff==0.9.10")
    call_count = len(calls)

    with pytest.raises(CliPackageCollisionError):
        install_cli_package("python", "ruff==0.9.10")

    assert len(calls) == call_count
    assert Path(installed["recordDir"]).is_dir()
    assert Path(installed["installDir"]).is_dir()


def test_failed_install_removes_unpublished_payload_and_record(
    package_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = 0

    def failing_run(argv, **kwargs):
        nonlocal call_count
        call_count += 1
        command = [str(item) for item in argv]
        if call_count == 1:
            payload_dir = Path(command[-1])
            _make_executable(cli_packages._venv_python(payload_dir))
            kwargs["stdout"].write(b"created")
            return subprocess.CompletedProcess(command, 0)
        kwargs["stderr"].write(b"failed")
        return subprocess.CompletedProcess(command, 2)

    monkeypatch.setattr(cli_packages.subprocess, "run", failing_run)

    with pytest.raises(CliPackageInstallError):
        install_cli_package("python", "ruff==0.9.10")

    assert call_count == 2
    assert list_cli_packages() == []
    assert list(package_root.iterdir()) == []


def test_uninstall_validates_manifest_containment_before_removal(
    package_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _mock_install_commands(monkeypatch, tmp_path)
    installed = install_cli_package("python", "ruff==0.9.10")
    manifest_path = Path(installed["manifestPath"])
    original_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    outside = tmp_path / "outside-payload"
    outside.mkdir()

    tampered_manifest = dict(original_manifest)
    tampered_manifest["installDir"] = str(outside)
    manifest_path.write_text(json.dumps(tampered_manifest), encoding="utf-8")

    with pytest.raises(CliPackageSafetyError):
        uninstall_cli_package(installed["packageId"])

    assert outside.is_dir()
    assert Path(installed["recordDir"]).is_dir()
    assert Path(installed["installDir"]).is_dir()

    manifest_path.write_text(json.dumps(original_manifest), encoding="utf-8")
    removed = uninstall_cli_package(installed["packageId"])

    assert removed["removed"] is True
    assert removed["status"] == "uninstalled"
    assert not Path(installed["recordDir"]).exists()
    assert not Path(installed["installDir"]).exists()
    assert list_cli_packages() == []


def test_list_rejects_unsafe_executable_records(
    package_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _mock_install_commands(monkeypatch, tmp_path)
    installed = install_cli_package("python", "ruff==0.9.10")
    manifest_path = Path(installed["manifestPath"])
    original = json.loads(manifest_path.read_text(encoding="utf-8"))
    executable = next(
        item for item in original["executables"] if item["name"] == "demo-cli"
    )

    unsafe_entries = [
        [executable, dict(executable)],
        [{"name": "renamed-cli", "path": executable["path"]}],
        [
            {
                "name": Path(original["installDir"]).name,
                "path": original["installDir"],
            }
        ],
    ]
    for entries in unsafe_entries:
        tampered = {**original, "executables": entries}
        manifest_path.write_text(json.dumps(tampered), encoding="utf-8")
        with pytest.raises(CliPackageManifestError):
            list_cli_packages()

    manifest_path.write_text(json.dumps(original), encoding="utf-8")
    assert list_cli_packages()[0]["packageId"] == installed["packageId"]


def test_uninstall_rejects_path_like_identifier(package_root: Path) -> None:
    with pytest.raises(CliPackageValidationError):
        uninstall_cli_package("../python-ruff-deadbeef0000")
    assert not package_root.exists()


def test_installer_output_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    oversized = "x" * (cli_packages.MAX_OUTPUT_CHARS + 100)

    def noisy_run(argv, **kwargs):
        kwargs["stdout"].write(oversized.encode("utf-8"))
        kwargs["stderr"].write(oversized.encode("utf-8"))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(
        cli_packages.subprocess,
        "run",
        noisy_run,
    )

    result = cli_packages._run_command(["fake-installer"])

    assert len(result["stdout"]) == cli_packages.MAX_OUTPUT_CHARS
    assert len(result["stderr"]) == cli_packages.MAX_OUTPUT_CHARS
    assert "...[truncated]..." in result["stdout"]


def test_installer_timeout_raises_typed_bounded_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oversized = "x" * (cli_packages.MAX_OUTPUT_CHARS + 100)

    def timeout(*args, **kwargs):
        kwargs["stdout"].write(oversized.encode("utf-8"))
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(cli_packages.subprocess, "run", timeout)

    with pytest.raises(CliPackageInstallError) as exc_info:
        cli_packages._run_command(["fake-installer"])

    assert len(exc_info.value.stdout) == cli_packages.MAX_OUTPUT_CHARS
    assert exc_info.value.argv == ["fake-installer"]
