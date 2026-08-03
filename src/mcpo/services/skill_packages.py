from __future__ import annotations

import base64
import binascii
import io
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterator, List

from mcpo.services.skills import (
    list_skills,
    skills_dir,
    validate_canonical_skill_document,
    validate_skill_id,
)


MAX_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_BASE64_CHARS = ((MAX_ARCHIVE_BYTES + 2) // 3) * 4 + 16
MAX_EXPANDED_BYTES = 25 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 1000
MAX_SKILL_BYTES = 256 * 1024
_PACKAGE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SCRIPT_SUFFIXES = {
    ".bat",
    ".cmd",
    ".exe",
    ".js",
    ".mjs",
    ".cjs",
    ".ps1",
    ".py",
    ".sh",
}
_WINDOWS_RESERVED_NAMES = (
    {"AUX", "CON", "NUL", "PRN"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)


class SkillPackageError(ValueError):
    pass


class SkillPackageConflict(SkillPackageError):
    pass


class SkillPackageNotFound(SkillPackageError):
    pass


def _package_id_from_filename(filename: str) -> str:
    basename = Path(filename or "").name
    lowered = basename.lower()
    if not lowered.endswith((".skill", ".zip")):
        raise SkillPackageError("Archive filename must end in .skill or .zip")
    for suffix in (".skill", ".zip"):
        if lowered.endswith(suffix):
            basename = basename[: -len(suffix)]
            break
    package_id = re.sub(r"[^a-z0-9]+", "-", basename.lower()).strip("-")
    if (
        not package_id
        or not _PACKAGE_ID.fullmatch(package_id)
        or package_id.upper() in _WINDOWS_RESERVED_NAMES
    ):
        raise SkillPackageError("Archive filename must contain a valid package name")
    return package_id


def _decode_archive(content_base64: str) -> bytes:
    if not isinstance(content_base64, str) or not content_base64:
        raise SkillPackageError("Archive content is required")
    if len(content_base64) > MAX_ARCHIVE_BASE64_CHARS:
        raise SkillPackageError(
            f"Archive exceeds the {MAX_ARCHIVE_BYTES} byte compressed limit"
        )
    try:
        payload = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SkillPackageError("Archive content is not valid base64") from exc
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise SkillPackageError(
            f"Archive exceeds the {MAX_ARCHIVE_BYTES} byte compressed limit"
        )
    return payload


def _safe_member_path(info: zipfile.ZipInfo) -> PurePosixPath:
    name = info.filename
    if not name or "\\" in name or "\x00" in name:
        raise SkillPackageError(f"Unsafe archive path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SkillPackageError(f"Unsafe archive path: {name}")
    if any(":" in part for part in path.parts):
        raise SkillPackageError(f"Unsafe archive path: {name}")
    if len(name) > 4096:
        raise SkillPackageError("Archive path exceeds 4096 characters")
    for part in path.parts:
        if (
            len(part) > 255
            or part != part.rstrip(" .")
            or any(ord(character) < 32 for character in part)
            or part.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES
        ):
            raise SkillPackageError(f"Unsafe archive path: {name}")
    mode = (info.external_attr >> 16) & 0o170000
    if stat.S_ISLNK(mode):
        raise SkillPackageError(f"Archive symlinks are not allowed: {name}")
    if info.flag_bits & 0x1:
        raise SkillPackageError(f"Encrypted archive members are not allowed: {name}")
    return path


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


@contextmanager
def _skill_package_lock(installed_root: Path) -> Iterator[None]:
    lock_path = installed_root / ".install.lock"
    try:
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError as exc:
        raise SkillPackageConflict(
            "Another skill package operation is already active"
        ) from exc
    except OSError as exc:
        raise SkillPackageError("Cannot acquire the skill package operation lock") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as lock_file:
            lock_file.write(str(os.getpid()))
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise SkillPackageError(
                "Cannot release the skill package operation lock"
            ) from exc


def _inspect_archive_bytes(filename: str, payload: bytes) -> Dict[str, Any]:
    package_id = _package_id_from_filename(filename)
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise SkillPackageError("Skill package must be a valid .skill or .zip archive") from exc

    with archive:
        infos = archive.infolist()
        file_infos = [info for info in infos if not info.is_dir()]
        if not file_infos:
            raise SkillPackageError("Skill package is empty")
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise SkillPackageError(
                f"Skill package exceeds the {MAX_ARCHIVE_ENTRIES} entry limit"
            )

        normalized_paths: Dict[str, PurePosixPath] = {}
        total_bytes = 0
        skill_entries: List[Dict[str, str]] = []
        skill_ids: set[str] = set()
        has_scripts = False
        warnings: List[str] = []

        for info in infos:
            member_path = _safe_member_path(info)
            key = str(member_path).casefold()
            if key in normalized_paths:
                raise SkillPackageError(
                    f"Archive contains duplicate paths: {member_path}"
                )
            normalized_paths[key] = member_path
            if info.is_dir():
                continue

            total_bytes += info.file_size
            if total_bytes > MAX_EXPANDED_BYTES:
                raise SkillPackageError(
                    f"Skill package exceeds the {MAX_EXPANDED_BYTES} byte expanded limit"
                )
            lowered_parts = {part.lower() for part in member_path.parts}
            if (
                "scripts" in lowered_parts
                or "hooks" in lowered_parts
                or member_path.suffix.lower() in _SCRIPT_SUFFIXES
                or member_path.name.lower() in {"package.json", "pyproject.toml"}
            ):
                has_scripts = True

            if member_path.name != "SKILL.md":
                continue
            if info.file_size > MAX_SKILL_BYTES:
                raise SkillPackageError(
                    f"{member_path} exceeds the {MAX_SKILL_BYTES} byte skill limit"
                )
            try:
                raw = archive.read(info).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SkillPackageError(f"{member_path} is not UTF-8") from exc
            except zipfile.BadZipFile as exc:
                raise SkillPackageError(f"{member_path} failed archive integrity checks") from exc
            try:
                validated = validate_canonical_skill_document(raw)
            except ValueError as exc:
                raise SkillPackageError(f"{member_path}: {exc}") from exc
            skill_id = validated["id"]
            if skill_id in skill_ids:
                raise SkillPackageError(
                    f"Archive contains duplicate skill ID '{skill_id}'"
                )
            skill_ids.add(skill_id)
            skill_entries.append(
                {
                    "id": skill_id,
                    "title": validated["title"],
                    "description": validated["description"].strip(),
                }
            )

        if not skill_entries:
            raise SkillPackageError("Archive contains no canonical SKILL.md files")
        if has_scripts:
            warnings.append(
                "Package contains scripts. MCPO installs them as inert resources and does not execute them."
            )

    destination = (skills_dir() / "installed" / package_id).resolve()
    existing_ids = {skill.id for skill in list_skills()}
    conflicts = sorted(existing_ids.intersection(skill_ids))
    if conflicts:
        warnings.append(
            "Skill IDs already present: " + ", ".join(conflicts)
        )
    return {
        "packageId": package_id,
        "filename": Path(filename).name,
        "skills": skill_entries,
        "fileCount": len(file_infos),
        "totalBytes": total_bytes,
        "hasScripts": has_scripts,
        "warnings": warnings,
        "conflicts": conflicts,
        "destination": str(destination),
        "installed": destination.exists(),
    }


def inspect_skill_archive(filename: str, content_base64: str) -> Dict[str, Any]:
    return _inspect_archive_bytes(filename, _decode_archive(content_base64))


def install_skill_archive(filename: str, content_base64: str) -> Dict[str, Any]:
    payload = _decode_archive(content_base64)
    installed_root = (skills_dir() / "installed").resolve()
    installed_root.mkdir(parents=True, exist_ok=True)
    with _skill_package_lock(installed_root):
        inspection = _inspect_archive_bytes(filename, payload)
        if inspection["installed"]:
            raise SkillPackageConflict(
                f"Skill package '{inspection['packageId']}' is already installed"
            )
        if inspection["conflicts"]:
            raise SkillPackageConflict(
                "Skill IDs already exist: " + ", ".join(inspection["conflicts"])
            )

        destination = (installed_root / inspection["packageId"]).resolve()
        if destination.parent != installed_root:
            raise SkillPackageError("Resolved package destination escaped the skills root")

        temporary = Path(
            tempfile.mkdtemp(prefix=f".{inspection['packageId']}-", dir=installed_root)
        ).resolve()
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                for info in archive.infolist():
                    member_path = _safe_member_path(info)
                    target = (temporary / Path(*member_path.parts)).resolve()
                    if target != temporary and not target.is_relative_to(temporary):
                        raise SkillPackageError(
                            f"Archive path escaped the package root: {member_path}"
                        )
                    if info.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)

            installed_at = datetime.now(timezone.utc).isoformat()
            manifest = {
                "id": inspection["packageId"],
                "filename": inspection["filename"],
                "installedAt": installed_at,
                "skills": inspection["skills"],
                "fileCount": inspection["fileCount"],
                "totalBytes": inspection["totalBytes"],
                "hasScripts": inspection["hasScripts"],
                "warnings": inspection["warnings"],
            }
            (temporary / "mcpo-package.json").write_text(
                json.dumps(manifest, indent=2),
                encoding="utf-8",
            )
            try:
                temporary.rename(destination)
            except FileExistsError as exc:
                raise SkillPackageConflict(
                    f"Skill package '{inspection['packageId']}' is already installed"
                ) from exc
        except zipfile.BadZipFile as exc:
            shutil.rmtree(temporary, ignore_errors=True)
            raise SkillPackageError(
                "Skill package failed archive integrity checks"
            ) from exc
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

        return {**manifest, "destination": str(destination)}


def list_skill_packages() -> List[Dict[str, Any]]:
    installed_root = (skills_dir() / "installed").resolve()
    if not installed_root.exists():
        return []
    packages: List[Dict[str, Any]] = []
    for package_dir in sorted(installed_root.iterdir(), key=lambda path: path.name):
        if package_dir.name.startswith("."):
            continue
        if not package_dir.is_dir() or _is_link_or_junction(package_dir):
            raise SkillPackageError(
                f"Invalid installed skill package directory: {package_dir}"
            )
        if not _PACKAGE_ID.fullmatch(package_dir.name):
            raise SkillPackageError(
                f"Invalid installed skill package ID: {package_dir.name}"
            )
        resolved_dir = package_dir.resolve()
        if resolved_dir.parent != installed_root:
            raise SkillPackageError(
                f"Installed skill package escaped the skills root: {package_dir.name}"
            )
        manifest_path = resolved_dir / "mcpo-package.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SkillPackageError(
                f"Invalid installed skill package manifest: {manifest_path}"
            ) from exc
        if not isinstance(manifest, dict):
            raise SkillPackageError(
                f"Installed skill package manifest must be an object: {manifest_path}"
            )
        if manifest.get("id") != package_dir.name:
            raise SkillPackageError(
                f"Installed skill package manifest ID mismatch: {manifest_path}"
            )
        if not isinstance(manifest.get("skills"), list):
            raise SkillPackageError(
                f"Installed skill package manifest has invalid skills: {manifest_path}"
            )
        for skill in manifest["skills"]:
            if not isinstance(skill, dict):
                raise SkillPackageError(
                    f"Installed skill package manifest has invalid skill metadata: {manifest_path}"
                )
            try:
                validate_skill_id(skill.get("id", ""))
            except ValueError as exc:
                raise SkillPackageError(
                    f"Installed skill package manifest has invalid skill ID: {manifest_path}"
                ) from exc
            if not isinstance(skill.get("title"), str) or not isinstance(
                skill.get("description"), str
            ):
                raise SkillPackageError(
                    f"Installed skill package manifest has invalid skill metadata: {manifest_path}"
                )
        packages.append(
            {
                **manifest,
                "destination": str(resolved_dir),
            }
        )
    return packages


def uninstall_skill_package(package_id: str) -> Dict[str, Any]:
    if not _PACKAGE_ID.fullmatch(package_id or ""):
        raise SkillPackageNotFound("Skill package not found")
    installed_root = (skills_dir() / "installed").resolve()
    if not installed_root.is_dir():
        raise SkillPackageNotFound(f"Skill package '{package_id}' not found")
    with _skill_package_lock(installed_root):
        destination = (installed_root / package_id).resolve()
        if destination.parent != installed_root:
            raise SkillPackageNotFound("Skill package not found")
        if not (destination / "mcpo-package.json").is_file():
            raise SkillPackageNotFound(f"Skill package '{package_id}' not found")
        shutil.rmtree(destination)
        return {"id": package_id, "uninstalled": True}
