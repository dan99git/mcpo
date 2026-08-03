from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class _IdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        for name, value in attrs:
            if name == "id" and value:
                self.ids.append(value)


def test_skills_installer_markup_has_unique_required_ids() -> None:
    parser = _IdCollector()
    parser.feed((ROOT / "static/ui/index.html").read_text(encoding="utf-8"))
    counts = Counter(parser.ids)
    duplicates = sorted(identifier for identifier, count in counts.items() if count > 1)
    assert duplicates == []

    required = {
        "skills-list",
        "skills-status",
        "skills-install-btn",
        "skills-installer",
        "skill-package-file-input",
        "skill-package-inspect-btn",
        "skill-package-install-btn",
        "skill-packages-list",
        "cli-package-kind",
        "cli-package-spec",
        "cli-package-plan-btn",
        "cli-package-install-btn",
        "cli-packages-list",
        "tool-package-file-input",
        "tool-package-preview-btn",
        "tool-package-preview",
        "tool-package-summary",
        "tool-package-manifests",
    }
    assert required.issubset(counts)


def test_skills_api_client_uses_registered_package_endpoints() -> None:
    api_source = (ROOT / "static/ui/js/core/api.js").read_text(encoding="utf-8")
    endpoints = {
        "/_meta/cli-packages",
        "/_meta/cli-packages/plan",
        "/_meta/cli-packages/install",
        "/_meta/skill-packages",
        "/_meta/skill-packages/inspect",
        "/_meta/skill-packages/install",
        "/_meta/tool-manifests/preview",
    }
    for endpoint in endpoints:
        assert endpoint in api_source

    page_source = (ROOT / "static/ui/js/pages/skills.js").read_text(encoding="utf-8")
    assert "MAX_SKILL_PACKAGE_BYTES = 10 * 1024 * 1024" in page_source
    assert "allow_scripts" in page_source
    assert "packageId" in page_source
    assert "previewToolManifestArchive" in page_source
    reset_start = page_source.index("function resetToolManifestPreview()")
    reset_end = page_source.index("function renderToolManifestPreview()", reset_start)
    assert "setButtonBusy(els.toolPreviewBtn, false" in page_source[reset_start:reset_end]
    html_source = (ROOT / "static/ui/index.html").read_text(encoding="utf-8")
    assert "nothing is installed, activated, or executed" in html_source
