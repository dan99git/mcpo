from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "static" / "ui" / "index.html").read_text(encoding="utf-8")
SETTINGS_JS = (ROOT / "static" / "ui" / "js" / "pages" / "settings.js").read_text(
    encoding="utf-8"
)


def test_settings_has_codex_oauth_key_controls():
    required_ids = [
        "settings-codex-key-status",
        "settings-codex-key-name",
        "settings-codex-key-expires",
        "settings-codex-key-scope-models",
        "settings-codex-key-scope-responses",
        "settings-codex-key-create",
        "settings-codex-key-created",
        "settings-codex-key-value",
        "settings-codex-key-copy",
        "settings-codex-key-dismiss",
        "settings-codex-key-list",
    ]
    for element_id in required_ids:
        assert f'id="{element_id}"' in INDEX
    assert "Copy this key now. It cannot be shown again." in INDEX
    assert 'id="settings-codex-key-value"' in INDEX
    secret_input = INDEX.split('id="settings-codex-key-value"', 1)[1].split(">", 1)[0]
    assert "readonly" in secret_input
    assert " value=" not in secret_input


def test_settings_uses_lifecycle_endpoints_without_persisting_plaintext():
    assert "const CODEX_ACCESS_KEYS_ENDPOINT = '/chat/providers/codex-oauth/access-keys';" in SETTINGS_JS
    assert "method: 'POST'" in SETTINGS_JS
    assert "encodeURIComponent(record.id) + '/revoke'" in SETTINGS_JS
    assert "els.codexKeyValue.value = data.key;" in SETTINGS_JS
    assert "settingsState.codexAccessKeys = data.keys;" in SETTINGS_JS
    assert "localStorage.setItem" not in SETTINGS_JS[
        SETTINGS_JS.index("// --- Codex OAuth API Keys ---") :
        SETTINGS_JS.index("// --- Chat Defaults ---")
    ]
    assert "name.textContent = record.name || record.id;" in SETTINGS_JS
    assert "metadata.textContent =" in SETTINGS_JS


def test_created_secret_is_rendered_without_a_followup_list_request():
    create_block = SETTINGS_JS[
        SETTINGS_JS.index("async function createCodexAccessKey()") :
        SETTINGS_JS.index("async function copyCodexAccessKey()")
    ]

    assert "els.codexKeyValue.value = data.key;" in create_block
    assert "data.record" in create_block
    assert "await loadCodexAccessKeys();" not in create_block
