from __future__ import annotations

import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


class _ElementCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: dict[str, tuple[str, dict[str, str | None]]] = {}
        self.duplicate_ids: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        attributes = dict(attrs)
        identifier = attributes.get("id")
        if identifier:
            if identifier in self.elements:
                self.duplicate_ids.append(identifier)
            self.elements[identifier] = (tag, attributes)

    handle_startendtag = handle_starttag


def _markup_elements() -> dict[str, tuple[str, dict[str, str | None]]]:
    parser = _ElementCollector()
    parser.feed((ROOT / "static/ui/index.html").read_text(encoding="utf-8"))
    return parser.elements


def _classic_script_function_collisions() -> dict[str, list[str]]:
    html = (ROOT / "static/ui/index.html").read_text(encoding="utf-8")
    script_sources = re.findall(r'<script\s+[^>]*src="([^"?]+)', html)
    owners: dict[str, str] = {}
    collisions: dict[str, list[str]] = {}
    declaration = re.compile(
        r"^\s*(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(",
        flags=re.MULTILINE,
    )
    for script_source in script_sources:
        script_path = ROOT / "static" / script_source.lstrip("/")
        source = script_path.read_text(encoding="utf-8")
        for match in declaration.finditer(source):
            name = match.group(1)
            previous = owners.get(name)
            if previous:
                collisions.setdefault(name, [previous]).append(script_source)
            owners[name] = script_source
    return collisions


def test_ui_markup_does_not_duplicate_element_ids() -> None:
    parser = _ElementCollector()
    parser.feed((ROOT / "static/ui/index.html").read_text(encoding="utf-8"))
    assert parser.duplicate_ids == []


def test_classic_script_load_order_has_no_function_name_collisions() -> None:
    assert _classic_script_function_collisions() == {}


def test_server_and_tool_controls_use_one_delegated_click_path() -> None:
    source = (ROOT / "static/ui/js/components/servers.js").read_text(
        encoding="utf-8"
    )
    create_start = source.index("function createServerItem")
    create_end = source.index("function updateServerVisuals", create_start)
    create_source = source[create_start:create_end]
    delegated_start = source.index("// Robust delegated handlers")
    delegated_source = source[delegated_start:]

    assert "toggle.onclick" not in create_source
    assert "toolTag.onclick" not in create_source
    assert "setAttribute('onclick'" not in create_source
    assert delegated_source.count("toggleServer(toggleEl, serverName)") == 1
    assert delegated_source.count("toggleTool(toolEl, toolName)") == 1


def test_chat_script_preserves_existing_classic_script_globals_at_runtime() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the classic-script runtime regression")
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const context = {
  window: {},
  document: { addEventListener() {} },
  console,
  structuredClone: global.structuredClone,
  setTimeout,
  clearTimeout,
  URL,
  TextDecoder,
  Headers,
  AbortController,
};
vm.createContext(context);
for (const path of ['static/ui/js/components/servers.js', 'static/ui/js/pages/skills.js']) {
  vm.runInContext(fs.readFileSync(path, 'utf8'), context, { filename: path });
}
const before = {
  renderServerList: context.renderServerList,
  getErrorMessage: context.getErrorMessage,
  formatBytes: context.formatBytes,
};
vm.runInContext(
  fs.readFileSync('static/ui/js/pages/chat.js', 'utf8'),
  context,
  { filename: 'static/ui/js/pages/chat.js' },
);
for (const [name, value] of Object.entries(before)) {
  if (context[name] !== value) throw new Error(`${name} was replaced by chat.js`);
}
for (const name of ['renderChatServerList', 'getChatErrorMessage', 'formatChatBytes']) {
  if (typeof context[name] !== 'function') throw new Error(`${name} is missing`);
}
"""
    completed = subprocess.run(
        [node, "-e", harness],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def _first_present(
    elements: dict[str, tuple[str, dict[str, str | None]]],
    *identifiers: str,
) -> tuple[str, dict[str, str | None]]:
    for identifier in identifiers:
        if identifier in elements:
            return elements[identifier]
    raise AssertionError(f"Missing required element; expected one of: {identifiers}")


def _css_rule_body(source: str, selector: str) -> str:
    match = re.search(
        rf"{re.escape(selector)}\s*\{{(?P<body>[^}}]*)\}}",
        source,
        flags=re.IGNORECASE,
    )
    assert match is not None, f"Missing CSS rule for {selector}"
    return match.group("body")


def test_chat_control_rail_stays_in_flow_at_desktop_and_mobile_widths() -> None:
    source = (ROOT / "static/ui/css/chat.css").read_text(encoding="utf-8")

    side_rule = _css_rule_body(source, ".chat-side")
    assert re.search(r"\bposition\s*:\s*absolute\b", side_rule) is None

    responsive_breakpoints = list(
        re.finditer(r"@media\s*\(\s*max-width\s*:\s*\d+px\s*\)", source)
    )
    assert responsive_breakpoints, "Chat CSS needs a narrow-screen breakpoint"
    responsive_source = source[responsive_breakpoints[-1].start() :]
    assert ".chat-page-layout" in responsive_source
    assert ".chat-side" in responsive_source


def test_model_picker_exposes_search_and_all_favorites_free_filters() -> None:
    elements = _markup_elements()

    assert "chat-model-search" in elements
    _first_present(elements, "chat-model-filter-all", "chat-model-category-all")
    _first_present(
        elements,
        "chat-model-filter-favorites",
        "chat-model-category-favorites",
    )
    _first_present(elements, "chat-model-filter-free", "chat-model-category-free")
    _first_present(elements, "chat-model-groups", "chat-model-list-items")


def test_model_picker_exposes_labeled_provider_selector_before_model_trigger() -> None:
    markup = (ROOT / "static/ui/index.html").read_text(encoding="utf-8")
    elements = _markup_elements()
    provider_tag, provider_attrs = _first_present(
        elements,
        "chat-provider-filter",
    )

    assert provider_tag == "select"
    assert provider_attrs.get("aria-label") or re.search(
        r'<label[^>]+for="chat-provider-filter"',
        markup,
    )
    assert "All providers" in markup
    assert markup.index('id="chat-provider-filter"') < markup.index(
        'id="chat-model-trigger"'
    )


def test_provider_filter_uses_live_catalog_counts_and_persists_browsing_choice() -> None:
    source = (ROOT / "static/ui/js/pages/chat.js").read_text(encoding="utf-8")

    assert "PROVIDER_FILTER_STORAGE_KEY" in source
    assert "mcpo-chat-provider-filter" in source
    assert "providerLabel" in source
    assert re.search(r"provider\.count\s*\}\)", source)
    assert "localStorage.getItem(PROVIDER_FILTER_STORAGE_KEY)" in source
    assert "localStorage.setItem(PROVIDER_FILTER_STORAGE_KEY" in source


def test_provider_filter_applies_before_search_category_and_favorite_filters() -> None:
    source = (ROOT / "static/ui/js/pages/chat.js").read_text(encoding="utf-8")
    render_start = source.index("function renderModelList")
    render_end = source.index("function providerQualifiedValue", render_start)
    render_source = source[render_start:render_end]

    provider_filter = render_source.index("chatState.providerFilter")
    favorites_filter = render_source.index("chatState.modelFilter === 'favorites'")
    free_filter = render_source.index("chatState.modelFilter === 'free'")
    search_filter = render_source.index("if (!query)")

    assert provider_filter < favorites_filter < free_filter < search_filter
    assert "model.provider !== chatState.providerFilter" in render_source
    assert "const key = providerQualifiedValue(model)" in render_source
    assert "favorites.has(key)" in render_source
    assert "const groups = new Map()" in render_source
    assert "model.providerLabel || model.provider" in render_source


def test_provider_change_opens_picker_without_selecting_or_patching_model() -> None:
    source = (ROOT / "static/ui/js/pages/chat.js").read_text(encoding="utf-8")
    handler_start = source.index("function handleProviderFilterChange")
    handler_end = source.index("function syncProviderFilterToSelectedModel", handler_start)
    handler_source = source[handler_start:handler_end]
    setter_start = source.index("function setBrowsingProviderFilter")
    setter_end = handler_start
    setter_source = source[setter_start:setter_end]

    assert "setBrowsingProviderFilter" in handler_source
    assert "setModelPanelOpen(true)" in handler_source
    for forbidden in (
        "selectedModelKey",
        "selectModel(",
        "updateSession(",
        "populateModelSelect(",
    ):
        assert forbidden not in handler_source
        assert forbidden not in setter_source


def test_provider_filter_syncs_to_active_model_on_load_and_explicit_selection() -> None:
    source = (ROOT / "static/ui/js/pages/chat.js").read_text(encoding="utf-8")
    init_start = source.index("async function initChatPage")
    init_end = source.index("function attachEventHandlers", init_start)
    init_source = source[init_start:init_end]
    select_start = source.index("async function selectModel")
    select_end = source.index("async function loadChatSettings", select_start)
    select_source = source[select_start:select_end]

    ensure_index = init_source.index("await ensureSession()")
    sync_index = init_source.index("syncProviderFilterToSelectedModel()")
    assert ensure_index < sync_index
    assert "syncProviderFilterToSelectedModel(model)" in select_source
    assert "const previousProviderFilter" in select_source
    assert "chatState.providerFilter = previousProviderFilter" in select_source
    assert select_source.index("syncProviderFilterToSelectedModel(model)") < select_source.index(
        "updateSession({ provider: model.provider, model: model.id })"
    )


def test_model_picker_uses_dialog_semantics_and_restores_trigger_focus() -> None:
    elements = _markup_elements()
    trigger_tag, trigger_attrs = _first_present(elements, "chat-model-trigger")
    dialog_tag, dialog_attrs = _first_present(elements, "chat-model-list")
    _, groups_attrs = _first_present(elements, "chat-model-groups")
    source = (ROOT / "static/ui/js/pages/chat.js").read_text(encoding="utf-8")
    panel_start = source.index("function setModelPanelOpen")
    panel_end = source.index("async function selectModel", panel_start)
    panel_source = source[panel_start:panel_end]

    assert trigger_tag == "button"
    assert trigger_attrs.get("aria-haspopup") == "dialog"
    assert trigger_attrs.get("aria-controls") == "chat-model-list"
    assert trigger_attrs.get("aria-expanded") in {"true", "false"}
    assert dialog_tag == "div"
    assert dialog_attrs.get("role") == "dialog"
    assert groups_attrs.get("role") is None
    assert "modelSearchInput?.focus()" in panel_source
    assert "modelTrigger?.focus()" in panel_source
    assert "setAttribute('role', 'option')" not in source
    assert "aria-current" in source


def test_model_picker_uses_provider_qualified_values_and_provider_groups() -> None:
    source = (ROOT / "static/ui/js/pages/chat.js").read_text(encoding="utf-8")

    assert "model.provider" in source
    provider_qualified_patterns = (
        r"\$\{\s*model\.provider\s*\}[^`\n]{0,8}\$\{\s*model\.id\s*\}",
        r"\[\s*model\.provider\s*,\s*model\.id\s*\]",
        r"model\.provider\s*\+\s*['\"][^'\"]+['\"]\s*\+\s*model\.id",
    )
    assert any(re.search(pattern, source) for pattern in provider_qualified_patterns)
    assert re.search(r"\b(?:isFree|is_free|free)\b", source)
    assert re.search(r"(?:group[^\n]{0,80}provider|provider[^\n]{0,80}group)", source, re.I)


def test_streaming_chat_request_includes_browser_authentication() -> None:
    source = (ROOT / "static/ui/js/pages/chat.js").read_text(encoding="utf-8")
    start = source.index("async function sendStreamingMessage")
    end = source.index("async function processSSEBuffer", start)
    streaming_source = source[start:end]

    uses_shared_helper = re.search(
        r"\b(?:fetchWithAuth|authenticatedFetch|fetchAuthenticated|apiFetch)\s*\(",
        streaming_source,
    )
    sends_explicit_bearer = all(
        token in streaming_source
        for token in ("mcpo-api-key", "Authorization", "Bearer")
    )
    assert uses_shared_helper or sends_explicit_bearer


def test_chat_composer_exposes_multi_file_input_and_attachment_preview() -> None:
    elements = _markup_elements()
    tag, attrs = _first_present(
        elements,
        "chat-attachment-input",
        "chat-file-input",
    )
    assert tag == "input"
    assert attrs.get("type") == "file"
    assert "multiple" in attrs
    _first_present(
        elements,
        "chat-attachment-preview",
        "chat-attachments-preview",
    )


def test_settings_exposes_masked_provider_management_and_default_system_prompt() -> None:
    elements = _markup_elements()

    _first_present(elements, "settings-provider-list", "settings-providers-list")
    _first_present(elements, "settings-provider-add", "settings-add-provider")
    _first_present(elements, "settings-provider-form", "settings-provider-editor")
    _first_present(
        elements,
        "settings-provider-key-status",
        "settings-provider-api-key-status",
    )

    key_tag, key_attrs = _first_present(
        elements,
        "settings-provider-api-key",
        "settings-provider-key-input",
    )
    assert key_tag == "input"
    assert key_attrs.get("type") == "password"
    assert key_attrs.get("value") in (None, "")

    prompt_tag, _ = _first_present(
        elements,
        "settings-system-prompt-input",
        "settings-default-system-prompt",
    )
    assert prompt_tag == "textarea"
    _first_present(
        elements,
        "settings-system-prompt-save",
        "settings-default-system-prompt-save",
    )


def test_settings_code_mode_toggle_is_a_keyboard_operable_button() -> None:
    elements = _markup_elements()
    tag, attrs = _first_present(elements, "settings-code-mode-toggle")
    source = (ROOT / "static/ui/js/pages/settings.js").read_text(encoding="utf-8")
    render_start = source.index("function renderCodeModeToggle")
    render_end = source.index("async function toggleCodeMode", render_start)
    render_source = source[render_start:render_end]

    assert tag == "button"
    assert attrs.get("type") == "button"
    assert attrs.get("aria-label")
    assert attrs.get("aria-pressed") in {"true", "false"}
    assert "aria-pressed" in render_source


def test_chat_payload_wires_attachments_agent_controls_and_tool_scope() -> None:
    elements = _markup_elements()
    source = (ROOT / "static/ui/js/pages/chat.js").read_text(encoding="utf-8")

    for identifier in (
        "chat-temperature",
        "chat-max-output-tokens",
        "chat-max-tool-rounds",
        "chat-include-reasoning",
        "chat-include-management-tools",
        "chat-all-servers",
        "chat-server-list",
    ):
        assert identifier in elements

    for request_field in (
        "mime_type",
        "max_output_tokens",
        "max_tool_rounds",
        "include_reasoning",
        "reasoning_effort",
        "server_allowlist",
        "include_management_tools",
    ):
        assert request_field in source


def test_provider_editor_calls_management_api_without_rehydrating_keys() -> None:
    source = (ROOT / "static/ui/js/pages/settings.js").read_text(encoding="utf-8")

    assert "fetchJson('/chat/providers')" in source
    assert "method: 'PUT'" in source
    assert "method: 'DELETE'" in source
    assert "/test`" in source
    assert "clearApiKey" in source
    assert "providerApiKey.value = ''" in source
    assert "provider.apiKey" not in source


def test_user_visible_chat_errors_are_rendered_as_text() -> None:
    source = (ROOT / "static/ui/js/pages/chat.js").read_text(encoding="utf-8")
    start = source.index("function showChatAlert")
    end = source.index("function safeJson", start)
    alert_source = source[start:end]

    assert ".textContent" in alert_source
    assert ".innerHTML" not in alert_source


def test_chat_does_not_keep_unsafe_unreachable_legacy_renderers() -> None:
    source = (ROOT / "static/ui/js/pages/chat.js").read_text(encoding="utf-8")

    assert "function renderSteps" not in source
    assert "function renderToolCatalog" not in source


def test_server_catalog_failure_is_distinct_from_valid_empty_and_fails_closed() -> None:
    source = (ROOT / "static/ui/js/pages/chat.js").read_text(encoding="utf-8")
    load_start = source.index("async function loadChatServers")
    load_end = source.index("function renderChatServerList", load_start)
    load_source = source[load_start:load_end]
    create_start = source.index("async function createSession")
    create_end = source.index("async function fetchSession", create_start)
    create_source = source[create_start:create_end]
    ensure_start = source.index("async function ensureSession")
    ensure_source = source[ensure_start:create_start]

    assert "fetchJson('/_meta/servers')" in load_source
    assert "response.ok" in load_source
    assert "data?.ok" in load_source
    assert "Array.isArray(data?.servers)" in load_source
    assert "serverCatalogLoaded" in load_source
    assert re.search(r"serverAllowlist\s*=\s*\[\]", load_source)
    assert "serverCatalogLoaded" in create_source
    assert re.search(r"server_allowlist\s*=.*\[\]", create_source, re.DOTALL)
    assert "!chatState.serverCatalogLoaded" in ensure_source
    assert re.search(r"updateSession\(\{\s*server_allowlist:\s*\[\]", ensure_source)


def test_provider_editor_supports_manual_model_ids_in_upsert_payload() -> None:
    elements = _markup_elements()
    source = (ROOT / "static/ui/js/pages/settings.js").read_text(encoding="utf-8")

    model_tag, _ = _first_present(
        elements,
        "settings-provider-models",
        "settings-provider-model-ids",
    )
    assert model_tag in {"textarea", "input"}
    assert "providerModels" in source
    assert re.search(r"payload\.models\s*=", source)
    assert re.search(r"model\??\.id", source)


def test_provider_url_validation_matches_backend_security_rules() -> None:
    source = (ROOT / "static/ui/js/pages/settings.js").read_text(encoding="utf-8")
    start = source.index("function settingsProviderBaseUrl")
    end = source.index("function addProviderBadge", start)
    validator = source[start:end]

    assert ".username" in validator
    assert ".password" in validator
    assert ".search" in validator
    assert ".hash" in validator
    assert "localhost" in validator
    assert re.search(r"===\s*127\b", validator)
    assert "::1" in validator
    assert re.search(r"scheme.*http", validator, re.IGNORECASE | re.DOTALL)


def test_catalog_only_provider_probe_is_not_displayed_as_connected() -> None:
    source = (ROOT / "static/ui/js/pages/settings.js").read_text(encoding="utf-8")
    start = source.index("async function testProvider")
    end = source.index("async function deleteProvider", start)
    probe_ui = source[start:end]

    assert "data?.verified === true" in probe_ui
    assert "data.verificationMode === 'live'" in probe_ui
    assert "data.status === 'connected'" in probe_ui
    assert "Configuration saved; live connection not verified." in probe_ui
