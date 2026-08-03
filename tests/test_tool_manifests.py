from __future__ import annotations

import base64
import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path
from textwrap import dedent

import pytest

from mcpo.services.tool_manifests import (
    ToolManifestArchiveError,
    preview_tool_manifest_archive,
)


PACKAGE_ID = "python-demo-123456789abc"


def _manifest(
    *,
    executable: str = "demo.exe",
    package_id: str = PACKAGE_ID,
) -> bytes:
    return dedent(
        f"""\
        schemaVersion: 1
        name: demo-tool
        description: Run the pinned demo command.
        runtime:
          packageId: {package_id}
          executable: {executable}
          argv:
            - "{{input.value}}"
            - --format
            - json
        inputSchema:
          type: object
          properties:
            value:
              type: string
              description: Value to process
          required: [value]
          additionalProperties: false
        capabilities:
          network: false
          filesystem:
            read: [input]
            write: [scratch, artifacts]
          environment: []
        limits:
          timeoutSeconds: 30
          outputBytes: 65536
        activation: disabled
        """
    ).encode("utf-8")


def _archive(entries: list[tuple[str, bytes]]) -> tuple[bytes, str]:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            info = zipfile.ZipInfo(name, date_time=(2024, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    payload = buffer.getvalue()
    return payload, base64.b64encode(payload).decode("ascii")


def _package(tmp_path: Path, *, executable: str = "demo.exe") -> dict:
    install_dir = tmp_path / "payload"
    executable_path = install_dir / "Scripts" / executable
    executable_path.parent.mkdir(parents=True)
    executable_path.write_bytes(b"demo")
    return {
        "packageId": PACKAGE_ID,
        "kind": "python",
        "spec": "demo==1.2.3",
        "name": "demo",
        "version": "1.2.3",
        "allowScripts": False,
        "isolation": "venv",
        "installDir": str(install_dir),
        "executables": [
            {"name": executable, "path": str(executable_path)},
        ],
    }


def _tree_snapshot(root: Path) -> list[tuple[str, bool, bytes]]:
    return [
        (
            str(path.relative_to(root)),
            path.is_dir(),
            b"" if path.is_dir() else path.read_bytes(),
        )
        for path in sorted(root.rglob("*"), key=lambda item: str(item))
    ]


def test_preview_hashes_resolves_and_never_executes_or_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_manifest = _manifest()
    raw_archive, encoded_archive = _archive([("proposal/TOOL.yaml", raw_manifest)])
    package = _package(tmp_path)
    before = _tree_snapshot(tmp_path)

    def fail_run(*args, **kwargs):
        raise AssertionError("TOOL.yaml preview must not execute subprocesses")

    monkeypatch.setattr(subprocess, "run", fail_run)
    preview = preview_tool_manifest_archive(
        "proposal.zip",
        encoded_archive,
        packages=[package],
    )

    assert _tree_snapshot(tmp_path) == before
    assert preview["archiveSha256"] == hashlib.sha256(raw_archive).hexdigest()
    assert preview["fileCount"] == 1
    assert preview["totalBytes"] == len(raw_manifest)
    manifest = preview["manifests"][0]
    assert manifest["manifestSha256"] == hashlib.sha256(raw_manifest).hexdigest()
    assert len(manifest["normalizedSha256"]) == 64
    assert manifest["valid"] is True
    assert manifest["resolved"] is True
    assert manifest["dependency"]["status"] == "resolved"
    assert manifest["commandPreview"] == [
        "demo.exe",
        "{input.value}",
        "--format",
        "json",
    ]
    assert manifest["executionEligible"] is False
    assert manifest["blockers"] == [
        "approval_registry_unavailable",
        "execution_worker_unavailable",
    ]
    assert str(tmp_path) not in json.dumps(manifest)


@pytest.mark.parametrize(
    ("raw_manifest", "expected_code"),
    [
        (b"schemaVersion: 1\nschemaVersion: 1\n", "duplicate_key"),
        (b"schemaVersion: &version 1\n", "yaml_anchor"),
        (b"schemaVersion: *version\n", "yaml_alias"),
        (b"schemaVersion: !!int '1'\n", "yaml_tag"),
        (b"schemaVersion: 1\nmerge: {<<: value}\n", "yaml_merge"),
        (
            b"schemaVersion: 1\nruntime:\n  executable: one\n  executable: two\n",
            "duplicate_key",
        ),
        (b"schemaVersion: 1\n---\nschemaVersion: 1\n", "multiple_documents"),
    ],
)
def test_preview_rejects_unsafe_yaml_constructs(
    raw_manifest: bytes,
    expected_code: str,
) -> None:
    _, encoded_archive = _archive([("TOOL.yaml", raw_manifest)])

    preview = preview_tool_manifest_archive(
        "unsafe.zip",
        encoded_archive,
        packages=[],
    )

    manifest = preview["manifests"][0]
    assert manifest["valid"] is False
    assert expected_code in {issue["code"] for issue in manifest["errors"]}
    assert manifest["dependency"]["status"] == "not_checked"


@pytest.mark.parametrize(
    ("raw_manifest", "expected_code"),
    [
        (_manifest() + b"unknownField: true\n", "unknown_field"),
        (
            _manifest().replace(b"schemaVersion: 1", b"schemaVersion: true"),
            "unsupported_schema_version",
        ),
        (
            _manifest().replace(
                b"argv:\n    - \"{input.value}\"\n    - --format\n    - json",
                b"argv: --help",
            ),
            "invalid_type",
        ),
        (_manifest().replace(b"network: false", b"network: true"), "network_denied"),
        (
            _manifest().replace(b"environment: []", b"environment: [SECRET]"),
            "environment_denied",
        ),
        (
            _manifest().replace(
                b"write: [scratch, artifacts]",
                b"write: [scratch, scratch]",
            ),
            "duplicate_item",
        ),
        (
            _manifest().replace(
                b"additionalProperties: false",
                b"additionalProperties: true",
            ),
            "additional_properties_denied",
        ),
        (
            _manifest().replace(b"executable: demo.exe", b"executable: ../demo.exe"),
            "invalid_executable",
        ),
        (
            _manifest().replace(b"activation: disabled", b"activation: enabled"),
            "activation_denied",
        ),
        (
            _manifest().replace(b'"{input.value}"', b'"--value={input.value}"'),
            "invalid_placeholder",
        ),
        (
            _manifest().replace(b"timeoutSeconds: 30", b"timeoutSeconds: true"),
            "invalid_integer",
        ),
        (
            _manifest().replace(b"{input.value}", b"{input.missing}"),
            "unknown_placeholder",
        ),
    ],
)
def test_preview_rejects_schema_and_policy_violations(
    raw_manifest: bytes,
    expected_code: str,
) -> None:
    _, encoded_archive = _archive([("TOOL.yaml", raw_manifest)])

    preview = preview_tool_manifest_archive(
        "invalid.zip",
        encoded_archive,
        packages=[],
    )

    manifest = preview["manifests"][0]
    assert manifest["valid"] is False
    assert expected_code in {issue["code"] for issue in manifest["errors"]}
    assert manifest["executionEligible"] is False


@pytest.mark.parametrize(
    ("raw_manifest", "expected_code"),
    [
        (b"\xff", "invalid_utf8"),
        (b"#" * (65_536 + 1), "manifest_too_large"),
        (
            b"schemaVersion: 1\nextra:\n"
            + b"".join((b"  " * depth) + b"item:\n" for depth in range(1, 11))
            + (b"  " * 11) + b"value\n",
            "manifest_too_deep",
        ),
        (
            b"schemaVersion: 1\nextra:\n"
            + b"".join(f"  - item-{index}\n".encode() for index in range(300)),
            "manifest_too_complex",
        ),
    ],
    ids=["invalid-utf8", "oversized", "too-deep", "too-complex"],
)
def test_preview_bounds_manifest_input(raw_manifest: bytes, expected_code: str) -> None:
    _, encoded_archive = _archive([("TOOL.yaml", raw_manifest)])

    preview = preview_tool_manifest_archive(
        "bounded.zip",
        encoded_archive,
        packages=[],
    )

    manifest = preview["manifests"][0]
    assert manifest["valid"] is False
    assert expected_code in {issue["code"] for issue in manifest["errors"]}


def test_preview_separates_lint_validity_from_dependency_resolution(
    tmp_path: Path,
) -> None:
    _, encoded_archive = _archive([("TOOL.yaml", _manifest())])

    missing_package = preview_tool_manifest_archive(
        "proposal.zip",
        encoded_archive,
        packages=[],
    )["manifests"][0]
    assert missing_package["valid"] is True
    assert missing_package["resolved"] is False
    assert missing_package["dependency"]["status"] == "package_missing"
    assert "dependency_unresolved" in missing_package["blockers"]

    wrong_executable = preview_tool_manifest_archive(
        "proposal.zip",
        encoded_archive,
        packages=[_package(tmp_path, executable="other.exe")],
    )["manifests"][0]
    assert wrong_executable["valid"] is True
    assert wrong_executable["resolved"] is False
    assert wrong_executable["dependency"]["status"] == "executable_missing"


def test_preview_orders_multiple_manifests_by_archive_path(tmp_path: Path) -> None:
    _, encoded_archive = _archive(
        [
            ("z/TOOL.yaml", _manifest().replace(b"name: demo-tool", b"name: z-tool")),
            ("a/TOOL.yaml", _manifest().replace(b"name: demo-tool", b"name: a-tool")),
        ]
    )

    preview = preview_tool_manifest_archive(
        "multi.zip",
        encoded_archive,
        packages=[_package(tmp_path)],
    )

    assert [item["path"] for item in preview["manifests"]] == [
        "a/TOOL.yaml",
        "z/TOOL.yaml",
    ]
    assert preview["valid"] is True


def test_preview_rejects_duplicate_tool_names(tmp_path: Path) -> None:
    _, encoded_archive = _archive(
        [
            ("one/TOOL.yaml", _manifest()),
            ("two/TOOL.yaml", _manifest()),
        ]
    )

    preview = preview_tool_manifest_archive(
        "duplicate.zip",
        encoded_archive,
        packages=[_package(tmp_path)],
    )

    assert preview["valid"] is False
    assert all(manifest["valid"] is False for manifest in preview["manifests"])
    assert all(
        "duplicate_tool_name" in {issue["code"] for issue in manifest["errors"]}
        for manifest in preview["manifests"]
    )


def test_preview_rejects_invalid_archives() -> None:
    with pytest.raises(ToolManifestArchiveError, match="valid base64"):
        preview_tool_manifest_archive("proposal.zip", "not-base64", packages=[])

    _, no_manifest = _archive([("tool.yaml", _manifest())])
    with pytest.raises(ToolManifestArchiveError, match="exact-case TOOL.yaml"):
        preview_tool_manifest_archive("proposal.zip", no_manifest, packages=[])

    _, traversal = _archive([("../TOOL.yaml", _manifest())])
    with pytest.raises(ToolManifestArchiveError, match="Unsafe archive path"):
        preview_tool_manifest_archive("proposal.zip", traversal, packages=[])
