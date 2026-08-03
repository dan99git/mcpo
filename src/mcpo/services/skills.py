from __future__ import annotations

import os
import re
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml

from mcpo.services.state import get_state_manager


_BOOL_TRUE = {"1", "true", "yes", "on"}
_CANONICAL_SKILL_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FRONTMATTER = re.compile(
    r"\A---[ \t]*\r?\n(?P<meta>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)",
    re.DOTALL,
)
_MAX_SKILL_BYTES = 256 * 1024
_MAX_SKILL_FILE_BYTES = _MAX_SKILL_BYTES + 16 * 1024
_MAX_SKILL_TITLE_CHARS = 200
_MAX_SKILL_DESCRIPTION_CHARS = 1024
logger = logging.getLogger(__name__)


@dataclass
class SkillDefinition:
    id: str
    title: str
    description: str
    content: str
    enabled: bool = True
    priority: int = 100
    scopes: List[str] | None = None
    providers: List[str] | None = None
    models: List[str] | None = None
    tags: List[str] | None = None
    source_path: str | None = None
    source_kind: str = "legacy"
    package_id: str = "legacy"
    format: str = "legacy"
    editable: bool = True
    resource_count: int = 0


def _skills_dir() -> Path:
    configured = (os.getenv("MCPO_SKILLS_DIR") or "skills").strip()
    return Path(configured).resolve()


def skills_dir() -> Path:
    """Return the configured skill-package root."""
    return _skills_dir()


def _bundled_skills_dir() -> Path:
    return (Path(__file__).resolve().parents[1] / "bundled_skills").resolve()


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in _BOOL_TRUE


def _parse_list_value(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw).strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    parts = [part.strip().strip("\"'") for part in text.split(",")]
    return [part for part in parts if part]


def parse_skill_document(raw: str) -> tuple[Dict[str, Any], str]:
    """Parse YAML frontmatter and return metadata plus the Markdown body."""
    text = (raw or "").lstrip("\ufeff")
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text
    try:
        parsed = yaml.safe_load(match.group("meta")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid skill frontmatter: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Skill frontmatter must be a YAML object")
    return dict(parsed), text[match.end() :].lstrip("\r\n")


def _parse_frontmatter(raw: str) -> tuple[Dict[str, Any], str]:
    return parse_skill_document(raw)


def _safe_skill_id(value: Any) -> str:
    sid = re.sub(
        r"[^0-9A-Za-z_-]",
        "-",
        str(value or "").strip().lower(),
    ).strip("-")
    return sid or "skill"


def validate_skill_id(value: str) -> str:
    sid = (value or "").strip()
    if not _CANONICAL_SKILL_ID.fullmatch(sid):
        raise ValueError(
            "Skill ID must use lowercase letters, numbers, and single hyphens"
        )
    return sid


def validate_canonical_skill_document(raw: str) -> Dict[str, Any]:
    """Validate canonical skill metadata and return normalized document fields."""
    meta, body = parse_skill_document(raw)
    raw_name = meta.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise ValueError("Canonical SKILL.md frontmatter requires name")
    skill_id = validate_skill_id(raw_name.strip())
    title = str(meta.get("title") or raw_name or skill_id)
    if len(title) > _MAX_SKILL_TITLE_CHARS:
        raise ValueError(f"Skill title exceeds {_MAX_SKILL_TITLE_CHARS} characters")
    raw_description = meta.get("description")
    if not isinstance(raw_description, str) or not raw_description.strip():
        raise ValueError("Canonical SKILL.md frontmatter requires description")
    description = str(raw_description)
    if len(description) > _MAX_SKILL_DESCRIPTION_CHARS:
        raise ValueError(
            f"Skill description exceeds {_MAX_SKILL_DESCRIPTION_CHARS} characters"
        )
    content = body.strip()
    if not content:
        raise ValueError("Canonical SKILL.md requires instruction content")
    return {
        "metadata": meta,
        "id": skill_id,
        "title": title,
        "description": description,
        "content": content,
    }


def _source_details(
    path: Path,
    root: Path,
    *,
    packaged: bool = False,
) -> tuple[str, str, str, bool]:
    relative = path.relative_to(root)
    canonical = path.name == "SKILL.md"
    parts = relative.parts
    if packaged:
        package_id = parts[0] if parts else path.parent.name
        skill_format = "canonical" if canonical else "legacy"
        return "bundled", package_id, skill_format, False
    if not canonical:
        return "legacy", "legacy", "legacy", True
    if parts and parts[0] == "local":
        return "local", "local", "canonical", True
    if len(parts) > 1 and parts[0] == "installed":
        return "installed", parts[1], "canonical", False
    package_id = parts[0] if parts else path.parent.name
    return "bundled", package_id, "canonical", False


def _build_skill_from_file(
    path: Path,
    *,
    root: Path | None = None,
    packaged: bool = False,
) -> Optional[SkillDefinition]:
    if not path.exists() or not path.is_file():
        return None
    if path.stat().st_size > _MAX_SKILL_FILE_BYTES:
        raise ValueError(f"Skill file exceeds {_MAX_SKILL_FILE_BYTES} bytes")
    raw = path.read_text(encoding="utf-8")
    source_root = root or _skills_dir()
    source_kind, package_id, skill_format, editable = _source_details(
        path,
        source_root,
        packaged=packaged,
    )
    canonical = skill_format == "canonical"
    fallback_id = path.parent.name if canonical else path.stem
    if canonical:
        validated = validate_canonical_skill_document(raw)
        meta = validated["metadata"]
        sid = validated["id"]
        title = validated["title"]
        description = validated["description"]
        body = validated["content"]
    else:
        meta, body = _parse_frontmatter(raw)
        sid = _safe_skill_id(str(meta.get("id") or fallback_id))
        title = str(meta.get("title") or meta.get("name") or sid)
        if len(title) > _MAX_SKILL_TITLE_CHARS:
            raise ValueError(f"Skill title exceeds {_MAX_SKILL_TITLE_CHARS} characters")
        description = str(meta.get("description") or "")
        if len(description) > _MAX_SKILL_DESCRIPTION_CHARS:
            raise ValueError(
                f"Skill description exceeds {_MAX_SKILL_DESCRIPTION_CHARS} characters"
            )
    enabled = _to_bool(meta.get("enabled"), default=source_kind in {"legacy", "local"})
    if source_kind in {"bundled", "installed"}:
        enabled = False
    try:
        priority = int(str(meta.get("priority", "100")))
    except ValueError:
        priority = 100
    scopes = _parse_list_value(meta.get("scopes"))
    providers = _parse_list_value(meta.get("providers"))
    models = _parse_list_value(meta.get("models"))
    tags = _parse_list_value(meta.get("tags"))
    resource_count = sum(
        1
        for candidate in path.parent.rglob("*")
        if candidate.is_file() and candidate != path
    )
    return SkillDefinition(
        id=sid,
        title=title,
        description=description,
        content=(body or "").strip(),
        enabled=enabled,
        priority=priority,
        scopes=scopes or None,
        providers=providers or None,
        models=models or None,
        tags=tags or None,
        source_path=str(path),
        source_kind=source_kind,
        package_id=package_id,
        format=skill_format,
        editable=editable,
        resource_count=resource_count,
    )


def scan_skills() -> tuple[List[SkillDefinition], List[Dict[str, str]]]:
    skills: List[SkillDefinition] = []
    issues: List[Dict[str, str]] = []
    configured_root = _skills_dir()
    packaged_root = _bundled_skills_dir()
    roots = [(configured_root, False)]
    if packaged_root != configured_root:
        roots.append((packaged_root, True))
    for root, packaged in roots:
        if not root.exists():
            continue
        candidates = list(root.glob("*.md")) + list(root.rglob("SKILL.md"))
        for path in sorted(set(candidates), key=lambda candidate: str(candidate).lower()):
            relative = path.relative_to(root)
            if any(part.startswith(".") for part in relative.parts):
                continue
            try:
                skill = _build_skill_from_file(
                    path,
                    root=root,
                    packaged=packaged,
                )
            except (OSError, UnicodeError, ValueError) as exc:
                issues.append({"path": str(path), "message": str(exc)})
                logger.warning("Skipping invalid skill at %s: %s", path, exc)
                continue
            if skill:
                skills.append(skill)

    source_order = {"local": 0, "legacy": 1, "installed": 2, "bundled": 3}
    skills.sort(
        key=lambda item: (
            source_order.get(item.source_kind, 99),
            item.priority,
            item.id,
            item.source_path or "",
        )
    )
    unique_skills: List[SkillDefinition] = []
    seen: Dict[str, SkillDefinition] = {}
    for skill in skills:
        existing = seen.get(skill.id)
        if existing:
            if existing.source_kind == "local" and skill.source_kind == "bundled":
                continue
            if existing.source_kind == skill.source_kind == "bundled":
                continue
            issues.append(
                {
                    "path": skill.source_path or "",
                    "message": (
                        f"Duplicate skill ID '{skill.id}' conflicts with "
                        f"{existing.source_path}"
                    ),
                }
            )
            continue
        seen[skill.id] = skill
        unique_skills.append(skill)

    state = get_state_manager()
    states = state.get_all_skill_states()
    for skill in unique_skills:
        override = states.get(skill.id, {})
        if "enabled" in override:
            skill.enabled = bool(override["enabled"])
    unique_skills.sort(key=lambda item: (item.priority, item.id))
    return unique_skills, issues


def list_skills() -> List[SkillDefinition]:
    return scan_skills()[0]


def get_skill(skill_id: str) -> Optional[SkillDefinition]:
    sid = _safe_skill_id(skill_id)
    for skill in list_skills():
        if skill.id == sid:
            return skill
    return None


def upsert_skill_file(*, skill_id: str, title: str, description: str, content: str) -> SkillDefinition:
    sid = validate_skill_id(skill_id)
    title = (title or "").strip()
    description = (description or "").strip()
    if not title:
        raise ValueError("Skill title is required")
    if len(title) > _MAX_SKILL_TITLE_CHARS:
        raise ValueError(f"Skill title exceeds {_MAX_SKILL_TITLE_CHARS} characters")
    if not description:
        raise ValueError("Skill description is required")
    if len(description) > _MAX_SKILL_DESCRIPTION_CHARS:
        raise ValueError(
            f"Skill description exceeds {_MAX_SKILL_DESCRIPTION_CHARS} characters"
        )
    if not (content or "").strip():
        raise ValueError("Skill content is required")
    encoded_content = (content or "").encode("utf-8")
    if len(encoded_content) > _MAX_SKILL_BYTES:
        raise ValueError(f"Skill content exceeds {_MAX_SKILL_BYTES} bytes")
    root = _skills_dir()
    existing = get_skill(sid)
    if existing and not existing.editable:
        raise PermissionError(
            f"Skill '{sid}' belongs to package '{existing.package_id}' and is read-only"
        )
    path = (
        Path(existing.source_path)
        if existing and existing.source_path
        else root / "local" / sid / "SKILL.md"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    root_resolved = root.resolve()
    parent_resolved = path.parent.resolve()
    if path.is_symlink() or not (
        parent_resolved == root_resolved
        or parent_resolved.is_relative_to(root_resolved)
    ):
        raise PermissionError("Skill path escaped the configured skills root")
    existing_meta: Dict[str, Any] = {}
    if path.exists():
        existing_meta, _ = parse_skill_document(path.read_text(encoding="utf-8"))

    canonical = path.name == "SKILL.md"
    meta = dict(existing_meta)
    if canonical:
        meta.pop("id", None)
        meta["name"] = sid
    else:
        meta["id"] = sid
    meta["title"] = title
    meta["description"] = description
    meta.setdefault("enabled", True)
    meta.setdefault("priority", 100)
    meta.setdefault("scopes", ["chat", "completions"])
    frontmatter = yaml.safe_dump(
        meta,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).strip()
    payload = f"---\n{frontmatter}\n---\n{(content or '').rstrip()}\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
    skill = _build_skill_from_file(path, root=root)
    if not skill:
        raise ValueError(f"Failed to load saved skill: {sid}")
    return skill


def delete_skill_file(skill_id: str) -> bool:
    sid = _safe_skill_id(skill_id)
    skill = get_skill(sid)
    if not skill or not skill.source_path:
        return False
    if not skill.editable:
        raise PermissionError(
            f"Skill '{sid}' belongs to package '{skill.package_id}' and is read-only"
        )
    if skill.resource_count:
        raise ValueError(
            f"Skill '{sid}' has package resources; uninstall its package instead"
        )
    path = Path(skill.source_path)
    root = _skills_dir().resolve()
    if path.is_symlink():
        raise PermissionError("Refusing to delete a linked skill path")
    resolved_path = path.resolve(strict=True)
    if not (
        resolved_path.parent == root
        or resolved_path.parent.is_relative_to(root)
    ):
        raise PermissionError("Skill path escaped the configured skills root")
    resolved_path.unlink()
    local_root = (_skills_dir() / "local").resolve()
    parent = path.parent.resolve()
    if parent != local_root and parent.is_relative_to(local_root):
        try:
            parent.rmdir()
        except OSError:
            pass
    return True


def _matches_scope(skill: SkillDefinition, scope: str) -> bool:
    if not skill.scopes:
        return True
    return scope in {s.strip().lower() for s in skill.scopes}


def _matches_provider(skill: SkillDefinition, provider: Optional[str]) -> bool:
    if not skill.providers or not provider:
        return True
    p = provider.strip().lower()
    allowed = {item.strip().lower() for item in skill.providers}
    return p in allowed


def _matches_model(skill: SkillDefinition, model: Optional[str]) -> bool:
    if not skill.models or not model:
        return True
    m = model.strip().lower()
    for rule in skill.models:
        r = rule.strip().lower()
        if not r:
            continue
        if r.endswith("*") and m.startswith(r[:-1]):
            return True
        if m == r:
            return True
    return False


def select_skills(
    *,
    scope: str,
    model: Optional[str],
    provider: Optional[str],
    requested_skill_ids: Optional[Sequence[str]] = None,
) -> List[SkillDefinition]:
    selected: List[SkillDefinition] = []
    requested = (
        {_safe_skill_id(item) for item in requested_skill_ids if str(item).strip()}
        if requested_skill_ids is not None
        else None
    )
    for skill in list_skills():
        if not skill.enabled:
            continue
        if requested is not None and skill.id not in requested:
            continue
        if not _matches_scope(skill, scope):
            continue
        if not _matches_provider(skill, provider):
            continue
        if not _matches_model(skill, model):
            continue
        selected.append(skill)
    selected.sort(key=lambda item: (item.priority, item.id))
    return selected


def compile_skills_system_prompt(
    *,
    scope: str,
    model: Optional[str],
    provider: Optional[str],
    requested_skill_ids: Optional[Sequence[str]] = None,
) -> str:
    skills = select_skills(
        scope=scope,
        model=model,
        provider=provider,
        requested_skill_ids=requested_skill_ids,
    )
    if not skills:
        return ""
    lines: List[str] = ["Agent Skills (system-managed instructions):"]
    for skill in skills:
        lines.append(f"\n[Skill: {skill.title} | id={skill.id}]")
        lines.append((skill.content or "").strip())
    return "\n".join(lines).strip()

