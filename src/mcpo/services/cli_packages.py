from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence


MANIFEST_FILENAME = "manifest.json"
MANIFEST_SCHEMA_VERSION = 1
INSTALL_TIMEOUT_SECONDS = 300
MAX_OUTPUT_CHARS = 16_384

_NAME_TOKEN = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
_PYTHON_VERSION = r"[0-9]+[A-Za-z0-9]*(?:[._+!-][A-Za-z0-9]+)*"
_PYTHON_SPEC_RE = re.compile(
    rf"^(?P<name>{_NAME_TOKEN})(?:\[(?P<extras>{_NAME_TOKEN}(?:,{_NAME_TOKEN})*)\])?"
    rf"==(?P<version>{_PYTHON_VERSION})$"
)
_NPM_TOKEN = r"[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?"
_SEMVER_PART = r"(?:0|[1-9][0-9]*)"
_SEMVER_LABEL = r"[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*"
_NPM_SPEC_RE = re.compile(
    rf"^(?P<name>(?:@{_NPM_TOKEN}/{_NPM_TOKEN}|{_NPM_TOKEN}))"
    rf"@(?P<version>{_SEMVER_PART}\.{_SEMVER_PART}\.{_SEMVER_PART}"
    rf"(?:-{_SEMVER_LABEL})?(?:\+{_SEMVER_LABEL})?)$"
)
_PACKAGE_ID_RE = re.compile(
    r"^(?:python|npm)-[a-z0-9](?:[a-z0-9-]{0,47})-[0-9a-f]{12}$"
)


class CliPackageError(Exception):
    """Base error for isolated CLI package operations."""


class CliPackageValidationError(CliPackageError):
    """Raised when a package kind, spec, or identifier is unsafe."""


class CliPackageCollisionError(CliPackageError):
    """Raised when a package or package operation already exists."""


class CliPackageExecutableNotFoundError(CliPackageError):
    """Raised when the required Python or npm executable is unavailable."""


class CliPackageInstallError(CliPackageError):
    """Raised when an installer command fails or times out."""

    def __init__(
        self,
        message: str,
        *,
        argv: Sequence[str] | None = None,
        returncode: int | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.argv = list(argv or [])
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class CliPackageNotFoundError(CliPackageError):
    """Raised when an installed package record does not exist."""


class CliPackageSafetyError(CliPackageError):
    """Raised when a filesystem target escapes the configured package root."""


class CliPackageManifestError(CliPackageError):
    """Raised when an installed package manifest is missing or invalid."""


@dataclass(frozen=True)
class _ParsedSpec:
    kind: str
    spec: str
    name: str
    version: str
    canonical_spec: str


def _packages_root() -> Path:
    configured = (os.getenv("MCPO_CLI_PACKAGES_DIR") or "").strip()
    return Path(configured or ".mcpo/cli-packages").expanduser().resolve()


def _parse_spec(kind: str, spec: str, allow_scripts: bool) -> _ParsedSpec:
    if type(allow_scripts) is not bool:
        raise CliPackageValidationError("allow_scripts must be a boolean")
    if kind not in {"python", "npm"}:
        raise CliPackageValidationError("kind must be exactly 'python' or 'npm'")
    if not isinstance(spec, str) or not spec or spec != spec.strip():
        raise CliPackageValidationError("spec must be a non-empty exact package spec")
    if len(spec) > 512:
        raise CliPackageValidationError("spec exceeds 512 characters")

    if kind == "python":
        match = _PYTHON_SPEC_RE.fullmatch(spec)
        if match is None:
            raise CliPackageValidationError(
                "Python specs must use exact name[extras]==version syntax"
            )
        name = re.sub(r"[-_.]+", "-", match.group("name")).lower()
        extras_raw = match.group("extras")
        extras = []
        if extras_raw:
            extras = sorted(
                {re.sub(r"[-_.]+", "-", item).lower() for item in extras_raw.split(",")}
            )
        version = match.group("version").lower()
        extras_suffix = f"[{','.join(extras)}]" if extras else ""
        canonical = f"{name}{extras_suffix}=={version}"
        return _ParsedSpec(kind, spec, name, version, canonical)

    match = _NPM_SPEC_RE.fullmatch(spec)
    if match is None:
        raise CliPackageValidationError(
            "npm specs must use exact name@x.y.z or @scope/name@x.y.z syntax"
        )
    name = match.group("name")
    version = match.group("version")
    return _ParsedSpec(kind, spec, name, version, f"{name}@{version}")


def _package_id(parsed: _ParsedSpec) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", parsed.name.lower()).strip("-")[:48]
    digest = hashlib.sha256(
        f"{parsed.kind}\0{parsed.canonical_spec}".encode("utf-8")
    ).hexdigest()[:12]
    return f"{parsed.kind}-{slug}-{digest}"


def _plan_from_parsed(parsed: _ParsedSpec, allow_scripts: bool) -> dict[str, Any]:
    root = _packages_root()
    package_id = _package_id(parsed)
    record_dir = root / package_id
    payload_dir = root / f".payload-{package_id}"
    return {
        "status": "planned",
        "packageId": package_id,
        "kind": parsed.kind,
        "spec": parsed.spec,
        "name": parsed.name,
        "version": parsed.version,
        "allowScripts": allow_scripts,
        "isolation": "venv" if parsed.kind == "python" else "npm-prefix",
        "root": str(root),
        "recordDir": str(record_dir),
        "installDir": str(payload_dir),
        "manifestPath": str(record_dir / MANIFEST_FILENAME),
        "exists": record_dir.exists()
        or record_dir.is_symlink()
        or payload_dir.exists()
        or payload_dir.is_symlink(),
    }


def plan_cli_package(kind: str, spec: str, allow_scripts: bool = False) -> dict[str, Any]:
    """Validate an exact package spec and return its isolated install plan."""
    parsed = _parse_spec(kind, spec, allow_scripts)
    return _plan_from_parsed(parsed, allow_scripts)


def _ensure_root(*, create: bool) -> Path:
    root = _packages_root()
    if create:
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CliPackageSafetyError(f"Cannot create CLI package root: {root}") from exc
    if root.exists() and not root.is_dir():
        raise CliPackageSafetyError(f"CLI package root is not a directory: {root}")
    return root.resolve()


def _assert_direct_child(root: Path, target: Path, *, must_exist: bool) -> Path:
    try:
        resolved = target.resolve(strict=must_exist)
    except OSError as exc:
        raise CliPackageSafetyError(f"Cannot resolve package path: {target}") from exc
    if resolved.parent != root:
        raise CliPackageSafetyError(f"Package path escapes configured root: {target}")
    return resolved


def _assert_contained(root: Path, target: Path, *, must_exist: bool) -> Path:
    try:
        resolved = target.resolve(strict=must_exist)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise CliPackageSafetyError(f"Path escapes its isolated environment: {target}") from exc
    return resolved


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


@contextmanager
def _package_lock(root: Path, package_id: str) -> Iterator[None]:
    lock_path = root / f".lock-{package_id}"
    _assert_direct_child(root, lock_path, must_exist=False)
    try:
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError as exc:
        raise CliPackageCollisionError(
            f"Another operation is active for package: {package_id}"
        ) from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as lock_file:
            lock_file.write(package_id)
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def _bounded_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    marker = "\n...[truncated]...\n"
    available = MAX_OUTPUT_CHARS - len(marker)
    head_chars = available // 2
    tail_chars = available - head_chars
    return text[:head_chars] + marker + text[-tail_chars:]


def _read_captured_output(handle) -> str:
    handle.flush()
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    if size <= MAX_OUTPUT_CHARS:
        handle.seek(0)
        return _bounded_output(handle.read())
    marker = b"\n...[truncated]...\n"
    available = MAX_OUTPUT_CHARS - len(marker)
    head_bytes = available // 2
    tail_bytes = available - head_bytes
    handle.seek(0)
    head = handle.read(head_bytes)
    handle.seek(-tail_bytes, os.SEEK_END)
    tail = handle.read(tail_bytes)
    return (head + marker + tail).decode("utf-8", errors="replace")


def _run_command(argv: Sequence[str]) -> dict[str, Any]:
    command = [str(item) for item in argv]
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            result = subprocess.run(
                command,
                stdout=stdout_file,
                stderr=stderr_file,
                timeout=INSTALL_TIMEOUT_SECONDS,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CliPackageInstallError(
                f"Installer timed out after {INSTALL_TIMEOUT_SECONDS} seconds",
                argv=command,
                stdout=_read_captured_output(stdout_file),
                stderr=_read_captured_output(stderr_file),
            ) from exc
        except FileNotFoundError as exc:
            raise CliPackageExecutableNotFoundError(
                f"Installer executable was not found: {command[0]}"
            ) from exc
        except OSError as exc:
            raise CliPackageInstallError(
                f"Installer could not start: {command[0]}", argv=command
            ) from exc

        stdout = _read_captured_output(stdout_file)
        stderr = _read_captured_output(stderr_file)
    if result.returncode != 0:
        raise CliPackageInstallError(
            f"Installer exited with code {result.returncode}",
            argv=command,
            returncode=result.returncode,
            stdout=stdout,
            stderr=stderr,
        )
    return {
        "argv": command,
        "returnCode": result.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }


def _python_executable() -> str:
    if not sys.executable:
        raise CliPackageExecutableNotFoundError("Current Python executable is unavailable")
    executable = Path(sys.executable).resolve()
    if not executable.is_file():
        raise CliPackageExecutableNotFoundError(
            f"Current Python executable was not found: {executable}"
        )
    return str(executable)


def _npm_executable() -> str:
    executable = shutil.which("npm")
    if not executable:
        raise CliPackageExecutableNotFoundError("npm executable was not found on PATH")
    resolved = Path(executable).resolve()
    if not resolved.is_file():
        raise CliPackageExecutableNotFoundError(
            f"npm executable was not found: {resolved}"
        )
    return str(resolved)


def _venv_python(payload_dir: Path) -> Path:
    if os.name == "nt":
        return payload_dir / "Scripts" / "python.exe"
    return payload_dir / "bin" / "python"


def _discover_executables(
    kind: str,
    payload_dir: Path,
    excluded_names: set[str] | None = None,
) -> list[dict[str, str]]:
    executable_dir = (
        _venv_python(payload_dir).parent
        if kind == "python"
        else payload_dir / "node_modules" / ".bin"
    )
    if not executable_dir.exists():
        return []
    _assert_contained(payload_dir.resolve(), executable_dir, must_exist=True)

    discovered: list[dict[str, str]] = []
    excluded = excluded_names or set()
    for entry in sorted(executable_dir.iterdir(), key=lambda item: item.name.lower()):
        if entry.name.casefold() in excluded:
            continue
        if not entry.is_file() and not entry.is_symlink():
            continue
        _assert_contained(payload_dir.resolve(), entry, must_exist=True)
        if os.name != "nt" and not os.access(entry, os.X_OK):
            continue
        discovered.append({"name": entry.name, "path": str(entry)})
    return discovered


def _write_manifest(record_stage: Path, manifest: dict[str, Any]) -> None:
    manifest_path = record_stage / MANIFEST_FILENAME
    try:
        with manifest_path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except (OSError, TypeError) as exc:
        raise CliPackageManifestError("Failed to write CLI package manifest") from exc


def _safe_remove_tree(root: Path, target: Path) -> None:
    if not target.exists() and not target.is_symlink():
        return
    if _is_link_or_junction(target):
        raise CliPackageSafetyError(f"Refusing to remove linked package path: {target}")
    _assert_direct_child(root, target, must_exist=True)
    try:
        shutil.rmtree(target)
    except OSError as exc:
        raise CliPackageSafetyError(f"Failed to remove package path: {target}") from exc


def _cleanup_failed_install(root: Path, paths: Sequence[Path], cause: Exception) -> None:
    failures: list[str] = []
    for path in paths:
        try:
            _safe_remove_tree(root, path)
        except CliPackageSafetyError as exc:
            failures.append(str(exc))
    if failures:
        raise CliPackageSafetyError("; ".join(failures)) from cause


def install_cli_package(
    kind: str, spec: str, allow_scripts: bool = False
) -> dict[str, Any]:
    """Install one exact package into an isolated, stable payload directory."""
    parsed = _parse_spec(kind, spec, allow_scripts)
    plan = _plan_from_parsed(parsed, allow_scripts)
    root = _ensure_root(create=True)
    package_id = plan["packageId"]
    record_dir = Path(plan["recordDir"])
    payload_dir = Path(plan["installDir"])
    _assert_direct_child(root, record_dir, must_exist=False)
    _assert_direct_child(root, payload_dir, must_exist=False)

    with _package_lock(root, package_id):
        if (
            record_dir.exists()
            or record_dir.is_symlink()
            or payload_dir.exists()
            or payload_dir.is_symlink()
        ):
            raise CliPackageCollisionError(f"Package already exists: {package_id}")

        installer = _python_executable() if kind == "python" else _npm_executable()
        commands: list[dict[str, Any]] = []
        base_executable_names: set[str] = set()
        record_stage: Path | None = None
        published = False
        try:
            payload_dir.mkdir(mode=0o700)
            if kind == "python":
                commands.append(
                    _run_command([installer, "-m", "venv", str(payload_dir)])
                )
                venv_python = _venv_python(payload_dir)
                if not venv_python.is_file():
                    raise CliPackageExecutableNotFoundError(
                        f"Virtual environment Python was not created: {venv_python}"
                    )
                _assert_contained(payload_dir.resolve(), venv_python, must_exist=True)
                base_executable_names = {
                    entry.name.casefold()
                    for entry in venv_python.parent.iterdir()
                }
                commands.append(
                    _run_command(
                        [
                            str(venv_python),
                            "-m",
                            "pip",
                            "install",
                            "--disable-pip-version-check",
                            "--no-input",
                            "--only-binary=:all:",
                            parsed.spec,
                        ]
                    )
                )
            else:
                npm_command = [
                    installer,
                    "install",
                    "--prefix",
                    str(payload_dir),
                    "--no-audit",
                    "--no-fund",
                ]
                if not allow_scripts:
                    npm_command.append("--ignore-scripts")
                npm_command.append(parsed.spec)
                commands.append(_run_command(npm_command))

            manifest = {
                "schemaVersion": MANIFEST_SCHEMA_VERSION,
                "status": "installed",
                "packageId": package_id,
                "kind": parsed.kind,
                "spec": parsed.spec,
                "name": parsed.name,
                "version": parsed.version,
                "allowScripts": allow_scripts,
                "isolation": plan["isolation"],
                "installedAt": datetime.now(timezone.utc).isoformat(),
                "root": str(root),
                "recordDir": str(record_dir),
                "installDir": str(payload_dir),
                "manifestPath": str(record_dir / MANIFEST_FILENAME),
                "executables": _discover_executables(
                    kind,
                    payload_dir,
                    excluded_names=base_executable_names,
                ),
            }

            record_stage = Path(
                tempfile.mkdtemp(prefix=f".record-{package_id}-", dir=root)
            )
            _assert_direct_child(root, record_stage, must_exist=True)
            _write_manifest(record_stage, manifest)
            if record_dir.exists() or record_dir.is_symlink():
                raise CliPackageCollisionError(f"Package record already exists: {package_id}")
            try:
                record_stage.rename(record_dir)
            except FileExistsError as exc:
                raise CliPackageCollisionError(
                    f"Package record already exists: {package_id}"
                ) from exc
            record_stage = None
            published = True
            return {**manifest, "commands": commands}
        except Exception as exc:
            cleanup_paths = []
            if record_stage is not None:
                cleanup_paths.append(record_stage)
            if not published:
                cleanup_paths.append(payload_dir)
            _cleanup_failed_install(root, cleanup_paths, exc)
            raise


def _validate_package_id(package_id: str) -> str:
    if not isinstance(package_id, str) or _PACKAGE_ID_RE.fullmatch(package_id) is None:
        raise CliPackageValidationError("Invalid CLI package identifier")
    return package_id


def _load_manifest(root: Path, record_dir: Path) -> dict[str, Any]:
    if _is_link_or_junction(record_dir):
        raise CliPackageSafetyError(f"Package record must not be a link: {record_dir}")
    _assert_direct_child(root, record_dir, must_exist=True)
    manifest_path = record_dir / MANIFEST_FILENAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CliPackageManifestError(f"Missing package manifest: {manifest_path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise CliPackageManifestError(f"Invalid package manifest: {manifest_path}") from exc
    if not isinstance(manifest, dict):
        raise CliPackageManifestError(f"Package manifest must be an object: {manifest_path}")

    package_id = record_dir.name
    required_types = {
        "schemaVersion": int,
        "status": str,
        "packageId": str,
        "kind": str,
        "spec": str,
        "name": str,
        "version": str,
        "allowScripts": bool,
        "isolation": str,
        "installedAt": str,
        "root": str,
        "recordDir": str,
        "installDir": str,
        "manifestPath": str,
        "executables": list,
    }
    for field, expected_type in required_types.items():
        if type(manifest.get(field)) is not expected_type:
            raise CliPackageManifestError(f"Invalid manifest field: {field}")
    if manifest["packageId"] != package_id:
        raise CliPackageManifestError("Manifest package identifier does not match record")
    if manifest["schemaVersion"] != MANIFEST_SCHEMA_VERSION:
        raise CliPackageManifestError("Unsupported package manifest schema")
    if manifest["status"] != "installed":
        raise CliPackageManifestError("Package manifest is not installed")

    try:
        parsed = _parse_spec(
            manifest["kind"], manifest["spec"], manifest["allowScripts"]
        )
    except CliPackageValidationError as exc:
        raise CliPackageManifestError("Manifest contains an invalid package spec") from exc
    if _package_id(parsed) != package_id:
        raise CliPackageManifestError("Manifest package spec does not match identifier")
    if manifest["name"] != parsed.name or manifest["version"] != parsed.version:
        raise CliPackageManifestError("Manifest package metadata does not match spec")
    expected_isolation = "venv" if parsed.kind == "python" else "npm-prefix"
    if manifest["isolation"] != expected_isolation:
        raise CliPackageManifestError("Manifest isolation mode does not match package kind")

    expected_record = root / package_id
    expected_payload = root / f".payload-{package_id}"
    if Path(manifest["root"]).resolve() != root:
        raise CliPackageSafetyError("Manifest root does not match configured package root")
    if Path(manifest["recordDir"]).resolve() != expected_record.resolve():
        raise CliPackageSafetyError("Manifest record path does not match package record")
    if Path(manifest["manifestPath"]).resolve() != (
        expected_record / MANIFEST_FILENAME
    ).resolve():
        raise CliPackageSafetyError("Manifest path does not match package record")
    payload_dir = Path(manifest["installDir"])
    if payload_dir.resolve() != expected_payload.resolve():
        raise CliPackageSafetyError("Manifest payload path does not match package identifier")
    if _is_link_or_junction(payload_dir):
        raise CliPackageSafetyError(f"Package payload must not be a link: {payload_dir}")
    _assert_direct_child(root, payload_dir, must_exist=True)
    executable_names: set[str] = set()
    for executable in manifest["executables"]:
        if not isinstance(executable, dict):
            raise CliPackageManifestError("Manifest executable entry must be an object")
        if type(executable.get("name")) is not str or type(executable.get("path")) is not str:
            raise CliPackageManifestError("Manifest executable entry is invalid")
        executable_name = executable["name"]
        if (
            not executable_name
            or any(token in executable_name for token in ("/", "\\", ":", "\x00"))
            or executable_name in executable_names
        ):
            raise CliPackageManifestError("Manifest executable name is invalid or duplicated")
        executable_names.add(executable_name)
        executable_path = Path(executable["path"])
        if executable_path.name != executable_name:
            raise CliPackageManifestError("Manifest executable name does not match its path")
        _assert_contained(payload_dir.resolve(), executable_path, must_exist=True)
        if not executable_path.is_file():
            raise CliPackageManifestError("Manifest executable target must be a file")
    return manifest


def list_cli_packages() -> list[dict[str, Any]]:
    """List atomically published package manifests."""
    root = _ensure_root(create=False)
    if not root.exists():
        return []
    packages: list[dict[str, Any]] = []
    for record_dir in sorted(root.iterdir(), key=lambda item: item.name):
        if _PACKAGE_ID_RE.fullmatch(record_dir.name) is None:
            continue
        if not record_dir.is_dir():
            raise CliPackageManifestError(f"Package record is not a directory: {record_dir}")
        packages.append(_load_manifest(root, record_dir))
    return packages


def uninstall_cli_package(package_id: str) -> dict[str, Any]:
    """Remove one published package after validating record and payload containment."""
    package_id = _validate_package_id(package_id)
    root = _ensure_root(create=False)
    if not root.exists():
        raise CliPackageNotFoundError(f"Package not found: {package_id}")
    record_dir = root / package_id

    with _package_lock(root, package_id):
        if not record_dir.exists() and not record_dir.is_symlink():
            raise CliPackageNotFoundError(f"Package not found: {package_id}")
        manifest = _load_manifest(root, record_dir)
        payload_dir = Path(manifest["installDir"])
        _assert_direct_child(root, payload_dir, must_exist=True)
        _assert_direct_child(root, record_dir, must_exist=True)
        _safe_remove_tree(root, payload_dir)
        _safe_remove_tree(root, record_dir)
        return {
            **manifest,
            "status": "uninstalled",
            "removed": True,
        }


__all__ = [
    "CliPackageError",
    "CliPackageValidationError",
    "CliPackageCollisionError",
    "CliPackageExecutableNotFoundError",
    "CliPackageInstallError",
    "CliPackageNotFoundError",
    "CliPackageSafetyError",
    "CliPackageManifestError",
    "plan_cli_package",
    "install_cli_package",
    "list_cli_packages",
    "uninstall_cli_package",
]
