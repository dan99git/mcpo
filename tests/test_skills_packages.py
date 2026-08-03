from __future__ import annotations

import base64
import io
import zipfile
from pathlib import Path

import pytest

from mcpo.services import skills as skills_service
from mcpo.services.skill_packages import (
    MAX_ARCHIVE_ENTRIES,
    SkillPackageConflict,
    SkillPackageError,
    inspect_skill_archive,
    install_skill_archive,
    list_skill_packages,
    uninstall_skill_package,
)
from mcpo.services.skills import (
    get_skill,
    list_skills,
    scan_skills,
    select_skills,
    upsert_skill_file,
)


class _SkillState:
    def __init__(self) -> None:
        self.states = {}

    def get_all_skill_states(self):
        return dict(self.states)


@pytest.fixture
def skill_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "skills"
    monkeypatch.setenv("MCPO_SKILLS_DIR", str(root))
    monkeypatch.setattr(
        "mcpo.services.skills.get_state_manager",
        lambda: _SkillState(),
    )
    return root


def _skill_document(
    name: str,
    *,
    description: str = "A test skill",
    enabled: bool | None = None,
) -> str:
    enabled_line = "" if enabled is None else f"enabled: {str(enabled).lower()}\n"
    return (
        "---\n"
        f"name: {name}\n"
        f"description: '{description}'\n"
        f"{enabled_line}"
        "---\n"
        f"# {name}\n\n"
        "Follow the test workflow.\n"
    )


def _archive_base64(files: dict[str, str]) -> str:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def test_discovery_supports_canonical_packages_and_safe_defaults(skill_root: Path) -> None:
    bundled = skill_root / "skill-creator" / "skills" / "skill-creator"
    bundled.mkdir(parents=True)
    (bundled / "SKILL.md").write_text(
        _skill_document("skill-creator", enabled=True),
        encoding="utf-8",
    )
    local = skill_root / "local" / "my-skill"
    local.mkdir(parents=True)
    (local / "SKILL.md").write_text(
        _skill_document("my-skill", description="Handles: complex YAML", enabled=True),
        encoding="utf-8",
    )

    skills, issues = scan_skills()
    by_id = {skill.id: skill for skill in skills}

    assert issues == []
    assert by_id["skill-creator"].source_kind == "bundled"
    assert by_id["skill-creator"].enabled is False
    assert by_id["skill-creator"].editable is False
    assert by_id["my-skill"].source_kind == "local"
    assert by_id["my-skill"].description == "Handles: complex YAML"
    assert by_id["my-skill"].enabled is True
    assert by_id["my-skill"].editable is True


def test_packaged_bundled_skills_are_discovered_as_read_only_fallback(
    skill_root: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    packaged_root = tmp_path / "site-packages" / "mcpo" / "bundled_skills"
    packaged_skill = packaged_root / "tool-maker" / "skills" / "tool-maker"
    packaged_skill.mkdir(parents=True)
    (packaged_skill / "SKILL.md").write_text(
        _skill_document("tool-maker"),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        skills_service,
        "_bundled_skills_dir",
        lambda: packaged_root,
    )

    skills, issues = scan_skills()

    assert issues == []
    assert [skill.id for skill in skills] == ["tool-maker"]
    assert skills[0].source_kind == "bundled"
    assert skills[0].editable is False
    assert skills[0].enabled is False


def test_local_skill_creation_uses_canonical_package_layout(skill_root: Path) -> None:
    skill = upsert_skill_file(
        skill_id="new-skill",
        title="New Skill",
        description="Created locally",
        content="# New Skill\n\nDo the work.",
    )

    expected = skill_root / "local" / "new-skill" / "SKILL.md"
    assert Path(skill.source_path) == expected
    assert expected.is_file()
    assert skill.format == "canonical"
    assert skill.editable is True
    assert get_skill("new-skill").content.startswith("# New Skill")

    with pytest.raises(ValueError, match="lowercase"):
        upsert_skill_file(
            skill_id="Bad Skill",
            title="Bad",
            description="",
            content="No.",
        )

    with pytest.raises(ValueError, match="description is required"):
        upsert_skill_file(
            skill_id="missing-description",
            title="Missing Description",
            description="",
            content="Instructions.",
        )


def test_invalid_canonical_metadata_is_reported(skill_root: Path) -> None:
    invalid = skill_root / "broken" / "skills" / "broken"
    invalid.mkdir(parents=True)
    (invalid / "SKILL.md").write_text(
        "---\nname: Bad Skill\ndescription: Invalid ID\n---\nInstructions.\n",
        encoding="utf-8",
    )

    skills, issues = scan_skills()

    assert skills == []
    assert len(issues) == 1
    assert "lowercase" in issues[0]["message"]


def test_explicit_empty_skill_selection_selects_none(skill_root: Path) -> None:
    upsert_skill_file(
        skill_id="active-skill",
        title="Active",
        description="Active skill",
        content="Use this.",
    )

    assert [skill.id for skill in select_skills(
        scope="chat",
        model=None,
        provider=None,
        requested_skill_ids=None,
    )] == ["active-skill"]
    assert select_skills(
        scope="chat",
        model=None,
        provider=None,
        requested_skill_ids=[],
    ) == []


def test_skill_archive_inspect_install_list_and_uninstall(skill_root: Path) -> None:
    encoded = _archive_base64(
        {
            "bundle/skills/demo/SKILL.md": _skill_document("demo"),
            "bundle/skills/demo/scripts/helper.py": "print('inert')\n",
            "bundle/skills/demo/references/notes.md": "Reference\n",
        }
    )

    inspection = inspect_skill_archive("demo-pack.skill", encoded)

    assert inspection["packageId"] == "demo-pack"
    assert inspection["skills"][0]["id"] == "demo"
    assert inspection["hasScripts"] is True
    assert inspection["installed"] is False
    assert inspection["warnings"]

    installed = install_skill_archive("demo-pack.skill", encoded)
    assert installed["id"] == "demo-pack"
    assert Path(installed["destination"]).is_dir()
    assert [package["id"] for package in list_skill_packages()] == ["demo-pack"]

    demo = get_skill("demo")
    assert demo is not None
    assert demo.source_kind == "installed"
    assert demo.package_id == "demo-pack"
    assert demo.enabled is False
    assert demo.editable is False
    assert demo.resource_count == 2

    result = uninstall_skill_package("demo-pack")
    assert result == {"id": "demo-pack", "uninstalled": True}
    assert list_skill_packages() == []


def test_skill_archive_flags_script_files_outside_scripts_directories(
    skill_root: Path,
) -> None:
    encoded = _archive_base64(
        {
            "bundle/SKILL.md": _skill_document("demo"),
            "bundle/helper.py": "print('inert')\n",
        }
    )

    inspection = inspect_skill_archive("code.skill", encoded)

    assert inspection["hasScripts"] is True
    assert inspection["warnings"]


def test_skill_package_list_reports_corrupt_manifests(skill_root: Path) -> None:
    package_dir = skill_root / "installed" / "broken-package"
    package_dir.mkdir(parents=True)
    (package_dir / "mcpo-package.json").write_text("not json", encoding="utf-8")

    with pytest.raises(SkillPackageError, match="Invalid installed.*manifest"):
        list_skill_packages()


def test_skill_archive_rejects_path_traversal(skill_root: Path) -> None:
    encoded = _archive_base64(
        {
            "../escape.txt": "bad",
            "skill/SKILL.md": _skill_document("demo"),
        }
    )

    with pytest.raises(SkillPackageError, match="Unsafe archive path"):
        inspect_skill_archive("unsafe.skill", encoded)
    assert not (skill_root.parent / "escape.txt").exists()


@pytest.mark.parametrize("unsafe_name", ["bundle/CON.txt", "bundle/trailing. "])
def test_skill_archive_rejects_unsafe_windows_paths(
    skill_root: Path,
    unsafe_name: str,
) -> None:
    encoded = _archive_base64(
        {
            "skill/SKILL.md": _skill_document("demo"),
            unsafe_name: "bad",
        }
    )

    with pytest.raises(SkillPackageError, match="Unsafe archive path"):
        inspect_skill_archive("unsafe.skill", encoded)


def test_skill_archive_requires_supported_extension(skill_root: Path) -> None:
    encoded = _archive_base64(
        {"skill/SKILL.md": _skill_document("demo")}
    )

    with pytest.raises(SkillPackageError, match=r"\.skill or \.zip"):
        inspect_skill_archive("demo.tar", encoded)

    with pytest.raises(SkillPackageError, match="valid package name"):
        inspect_skill_archive("CON.skill", encoded)


def test_skill_archive_refuses_existing_skill_ids(skill_root: Path) -> None:
    upsert_skill_file(
        skill_id="demo",
        title="Demo",
        description="Existing",
        content="Existing instructions.",
    )
    encoded = _archive_base64(
        {"bundle/SKILL.md": _skill_document("demo")}
    )

    inspection = inspect_skill_archive("other.skill", encoded)
    assert inspection["conflicts"] == ["demo"]
    with pytest.raises(SkillPackageConflict, match="already exist"):
        install_skill_archive("other.skill", encoded)


def test_skill_archive_rejects_metadata_discovery_would_reject(
    skill_root: Path,
) -> None:
    encoded = _archive_base64(
        {
            "bundle/SKILL.md": _skill_document(
                "demo",
                description="x" * 1025,
            )
        }
    )

    with pytest.raises(SkillPackageError, match="description exceeds 1024"):
        inspect_skill_archive("oversized.skill", encoded)


def test_skill_archive_limit_counts_directory_entries(skill_root: Path) -> None:
    files = {
        f"empty/{index}/": ""
        for index in range(MAX_ARCHIVE_ENTRIES)
    }
    files["bundle/SKILL.md"] = _skill_document("demo")
    encoded = _archive_base64(files)

    with pytest.raises(SkillPackageError, match="entry limit"):
        inspect_skill_archive("too-many-entries.skill", encoded)


def test_skill_package_install_respects_global_operation_lock(
    skill_root: Path,
) -> None:
    installed_root = skill_root / "installed"
    installed_root.mkdir(parents=True)
    (installed_root / ".install.lock").write_text("active", encoding="utf-8")
    encoded = _archive_base64(
        {"bundle/SKILL.md": _skill_document("demo")}
    )

    with pytest.raises(SkillPackageConflict, match="operation is already active"):
        install_skill_archive("locked.skill", encoded)
    assert not (installed_root / "locked").exists()
