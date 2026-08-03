/* MCPO Settings Page */

const settingsState = {
    initialized: false,
    codeModeEnabled: false,
    providers: [],
    selectedProviderId: null,
    editingNewProvider: false,
    originalProviderModelIds: [],
    codexAccessKeys: [],
    codexOAuthReady: false,
    chatSettings: null,
};

function getSettingsElements() {
    return {
        codeModeToggle: document.getElementById('settings-code-mode-toggle'),
        codeModeHint: document.getElementById('settings-code-mode-hint'),
        apiKeyInput: document.getElementById('settings-api-key-input'),
        apiKeyReveal: document.getElementById('settings-api-key-reveal'),
        apiKeySave: document.getElementById('settings-api-key-save'),
        apiKeyHint: document.getElementById('settings-api-key-hint'),
        themeBtn: document.getElementById('settings-theme-btn'),
        clearStorageBtn: document.getElementById('settings-clear-storage'),
        providerAdd: document.getElementById('settings-provider-add'),
        providerStatus: document.getElementById('settings-provider-status'),
        providerList: document.getElementById('settings-provider-list'),
        providerForm: document.getElementById('settings-provider-form'),
        providerFormTitle: document.getElementById('settings-provider-form-title'),
        providerCancel: document.getElementById('settings-provider-cancel'),
        providerId: document.getElementById('settings-provider-id'),
        providerName: document.getElementById('settings-provider-name'),
        providerKind: document.getElementById('settings-provider-kind'),
        providerBilling: document.getElementById('settings-provider-billing'),
        providerBaseUrl: document.getElementById('settings-provider-base-url'),
        providerCapabilities: document.getElementById('settings-provider-capabilities'),
        providerModels: document.getElementById('settings-provider-models'),
        providerApiKey: document.getElementById('settings-provider-api-key'),
        providerKeyStatus: document.getElementById('settings-provider-key-status'),
        providerEnabled: document.getElementById('settings-provider-enabled'),
        providerClearKey: document.getElementById('settings-provider-clear-key'),
        providerDelete: document.getElementById('settings-provider-delete'),
        providerTest: document.getElementById('settings-provider-test'),
        providerSave: document.getElementById('settings-provider-save'),
        codexKeyStatus: document.getElementById('settings-codex-key-status'),
        codexKeyName: document.getElementById('settings-codex-key-name'),
        codexKeyExpires: document.getElementById('settings-codex-key-expires'),
        codexKeyScopeModels: document.getElementById('settings-codex-key-scope-models'),
        codexKeyScopeResponses: document.getElementById('settings-codex-key-scope-responses'),
        codexKeyCreate: document.getElementById('settings-codex-key-create'),
        codexKeyCreated: document.getElementById('settings-codex-key-created'),
        codexKeyValue: document.getElementById('settings-codex-key-value'),
        codexKeyCopy: document.getElementById('settings-codex-key-copy'),
        codexKeyDismiss: document.getElementById('settings-codex-key-dismiss'),
        codexKeyList: document.getElementById('settings-codex-key-list'),
        codexKeyLockdown: document.getElementById('settings-codex-key-lockdown'),
        systemPromptInput: document.getElementById('settings-system-prompt-input'),
        systemPromptSave: document.getElementById('settings-system-prompt-save'),
        systemPromptStatus: document.getElementById('settings-system-prompt-status'),
        chatTemperature: document.getElementById('settings-chat-temperature'),
        chatMaxOutput: document.getElementById('settings-chat-max-output'),
        chatMaxRounds: document.getElementById('settings-chat-max-rounds'),
        chatReasoning: document.getElementById('settings-chat-reasoning'),
        chatReasoningEffort: document.getElementById('settings-chat-reasoning-effort'),
        chatManagementTools: document.getElementById('settings-chat-management-tools'),
    };
}

// --- Code Mode ---

async function loadCodeModeState() {
    try {
        const { data } = await fetchJson('/_meta/code-mode');
        if (data && data.ok !== undefined) {
            settingsState.codeModeEnabled = !!data.enabled;
        }
    } catch (e) {
        console.warn('[SETTINGS] Failed to load code mode state:', e);
    }
    renderCodeModeToggle();
}

function renderCodeModeToggle() {
    const els = getSettingsElements();
    if (!els.codeModeToggle) return;
    els.codeModeToggle.classList.toggle('on', settingsState.codeModeEnabled);
    els.codeModeToggle.setAttribute('aria-pressed', String(settingsState.codeModeEnabled));
    els.codeModeToggle.setAttribute(
        'aria-label',
        settingsState.codeModeEnabled ? 'Disable Code Mode' : 'Enable Code Mode',
    );
    if (els.codeModeHint) {
        els.codeModeHint.textContent = settingsState.codeModeEnabled
            ? 'Code mode is ON — clients see search_tools + execute_tool only.'
            : 'Code mode is OFF — clients see all individual tools.';
        els.codeModeHint.classList.toggle('hint-active', settingsState.codeModeEnabled);
    }
}

async function toggleCodeMode() {
    const newState = !settingsState.codeModeEnabled;
    const els = getSettingsElements();

    // Optimistic UI
    settingsState.codeModeEnabled = newState;
    renderCodeModeToggle();

    try {
        const { data } = await fetchJson('/_meta/code-mode', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: newState }),
        });
        if (!data || !data.ok) {
            // Revert
            settingsState.codeModeEnabled = !newState;
            renderCodeModeToggle();
            console.error('[SETTINGS] Failed to toggle code mode:', data);
        }
    } catch (e) {
        settingsState.codeModeEnabled = !newState;
        renderCodeModeToggle();
        console.error('[SETTINGS] Error toggling code mode:', e);
    }
}

// --- API Key ---

function loadApiKeyState() {
    const els = getSettingsElements();
    if (!els.apiKeyInput) return;
    const stored = localStorage.getItem('mcpo-api-key') || '';
    els.apiKeyInput.value = stored;
    updateApiKeyHint(stored);
}

function updateApiKeyHint(key) {
    const els = getSettingsElements();
    if (!els.apiKeyHint) return;
    if (key) {
        els.apiKeyHint.textContent = 'API key is set. All requests will include Bearer authentication.';
        els.apiKeyHint.classList.add('hint-active');
    } else {
        els.apiKeyHint.textContent = 'No API key configured. Requests are unauthenticated.';
        els.apiKeyHint.classList.remove('hint-active');
    }
}

function saveApiKey() {
    const els = getSettingsElements();
    if (!els.apiKeyInput) return;
    const key = els.apiKeyInput.value.trim();
    if (key) {
        localStorage.setItem('mcpo-api-key', key);
    } else {
        localStorage.removeItem('mcpo-api-key');
    }
    updateApiKeyHint(key);
    // Brief visual feedback
    if (els.apiKeySave) {
        const original = els.apiKeySave.textContent;
        els.apiKeySave.textContent = 'Saved';
        setTimeout(() => { els.apiKeySave.textContent = original; }, 1200);
    }
}

function toggleApiKeyReveal() {
    const els = getSettingsElements();
    if (!els.apiKeyInput || !els.apiKeyReveal) return;
    const isPassword = els.apiKeyInput.type === 'password';
    els.apiKeyInput.type = isPassword ? 'text' : 'password';
    els.apiKeyReveal.textContent = isPassword ? 'Hide' : 'Show';
}

// --- Model Providers ---

function settingsApiError(response, data, fallback) {
    if (typeof getApiErrorMessage === 'function') {
        return getApiErrorMessage(response, data, fallback);
    }
    if (typeof data?.detail === 'string' && data.detail) return data.detail;
    if (typeof data?.error === 'string' && data.error) return data.error;
    if (typeof data?.error?.message === 'string' && data.error.message) return data.error.message;
    return response?.status ? `${fallback} (HTTP ${response.status})` : fallback;
}

function setProviderStatus(message = '', variant = '') {
    const { providerStatus } = getSettingsElements();
    if (!providerStatus) return;
    providerStatus.textContent = message;
    providerStatus.classList.remove('error', 'success');
    if (variant === 'error' || variant === 'success') {
        providerStatus.classList.add(variant);
    }
}

async function loadProviders() {
    const { response, data } = await fetchJson('/chat/providers');
    if (!response.ok || data?.ok !== true || !Array.isArray(data.providers)) {
        settingsState.providers = [];
        renderProviderList();
        throw new Error(settingsApiError(response, data, 'Could not load model providers'));
    }
    settingsState.providers = data.providers;
    renderProviderList();
}

function providerBillingLabel(billing) {
    return {
        free: 'Free models',
        free_rate_limited: 'Free tier',
        trial_credit: 'Trial credit',
        local: 'Local',
        paid: 'Paid',
        unknown: 'Billing unknown',
    }[billing] || billing || 'Billing unknown';
}

function settingsSafeHttpUrl(raw) {
    if (!raw) return '';
    try {
        const url = new URL(raw);
        return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
    } catch {
        return '';
    }
}

function settingsProviderBaseUrl(raw) {
    const candidate = String(raw || '').trim().replace(/\/+$/, '');
    let parsed;
    try {
        parsed = new URL(candidate);
    } catch {
        throw new Error('Provider base URL must be an absolute HTTP or HTTPS URL.');
    }
    const scheme = parsed.protocol.toLowerCase();
    if (!['http:', 'https:'].includes(scheme) || !parsed.hostname) {
        throw new Error('Provider base URL must be an absolute HTTP or HTTPS URL.');
    }
    const authority = candidate.match(/^[a-z][a-z0-9+.-]*:\/\/([^/?#]*)/i)?.[1] || '';
    if (authority.includes('@') || parsed.username || parsed.password) {
        throw new Error('Provider base URL must not contain credentials.');
    }
    if (parsed.search) {
        throw new Error('Provider base URL must not contain a query string.');
    }
    if (parsed.hash) {
        throw new Error('Provider base URL must not contain a fragment.');
    }
    let rawHostname = authority;
    if (rawHostname.startsWith('[')) {
        rawHostname = rawHostname.slice(1, rawHostname.indexOf(']'));
    } else {
        rawHostname = rawHostname.split(':', 1)[0];
    }
    const hostname = rawHostname.toLowerCase();
    const ipv4Parts = hostname.split('.');
    const ipv4Loopback = ipv4Parts.length === 4
        && ipv4Parts.every((part) => /^\d{1,3}$/.test(part)
            && Number(part) <= 255
            && part === String(Number(part)))
        && Number(ipv4Parts[0]) === 127;
    const normalizedHostname = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, '');
    const loopback = hostname === 'localhost'
        || ipv4Loopback
        || (hostname.includes(':') && normalizedHostname === '::1');
    if (scheme === 'http:' && !loopback) {
        throw new Error('Provider base URL must use HTTPS unless it targets a loopback host.');
    }
    return candidate;
}

function addProviderBadge(container, label, className = '') {
    const badge = document.createElement('span');
    badge.className = `settings-provider-badge ${className}`.trim();
    badge.textContent = label;
    container.appendChild(badge);
}

function renderProviderList() {
    const { providerList } = getSettingsElements();
    if (!providerList) return;
    providerList.innerHTML = '';
    if (!settingsState.providers.length) {
        const empty = document.createElement('div');
        empty.className = 'empty-state';
        empty.textContent = 'No providers are available.';
        providerList.appendChild(empty);
        return;
    }

    settingsState.providers.forEach((provider) => {
        const card = document.createElement('article');
        card.className = 'settings-provider-card';
        card.classList.toggle('is-configured', provider.configured === true);
        card.classList.toggle('is-disabled', provider.enabled === false);

        const header = document.createElement('div');
        header.className = 'settings-provider-card-header';
        const title = document.createElement('div');
        title.className = 'settings-provider-card-title';
        const name = document.createElement('strong');
        name.textContent = provider.displayName || provider.id;
        const id = document.createElement('span');
        id.textContent = provider.id;
        title.append(name, id);
        const badges = document.createElement('div');
        badges.className = 'settings-provider-badges';
        if (provider.configured) addProviderBadge(badges, 'Ready', 'configured');
        else addProviderBadge(badges, 'Setup required');
        if (provider.keySource === 'environment') addProviderBadge(badges, 'Environment key', 'environment');
        else if (provider.keySource === 'saved') addProviderBadge(badges, 'Saved key', 'configured');
        else if (provider.supportsKeyless) addProviderBadge(badges, 'Keyless');
        const billingClass = ['free', 'free_rate_limited'].includes(provider.billing)
            ? 'free'
            : provider.billing === 'local' ? 'local' : '';
        addProviderBadge(badges, providerBillingLabel(provider.billing), billingClass);
        header.append(title, badges);

        const meta = document.createElement('div');
        meta.className = 'settings-provider-meta';
        const modelCount = Array.isArray(provider.models) ? provider.models.length : 0;
        meta.textContent = `${provider.kind || 'openai_compatible'} · ${modelCount} models · ${provider.baseUrl || 'No base URL'}`;

        const actions = document.createElement('div');
        actions.className = 'settings-provider-card-actions';
        const edit = document.createElement('button');
        edit.type = 'button';
        edit.className = 'btn';
        edit.textContent = 'Edit';
        edit.addEventListener('click', () => openProviderEditor(provider));
        const test = document.createElement('button');
        test.type = 'button';
        test.className = 'btn';
        test.textContent = 'Test';
        test.disabled = provider.configured !== true;
        test.addEventListener('click', () => testProvider(provider.id));
        actions.append(edit, test);
        const docsUrl = settingsSafeHttpUrl(provider.docsUrl);
        if (docsUrl) {
            const docs = document.createElement('a');
            docs.className = 'btn';
            docs.href = docsUrl;
            docs.target = '_blank';
            docs.rel = 'noopener noreferrer';
            docs.textContent = 'Docs';
            actions.appendChild(docs);
        }
        card.append(header, meta, actions);
        providerList.appendChild(card);
    });
}

function providerKeyStatus(provider) {
    if (!provider) return 'Enter a key if this API requires one. It will not be returned to the browser.';
    if (provider.credentialBoundaryLocked) return 'Uses the local Codex OAuth login. Connection and credential fields are locked.';
    if (provider.keySource === 'saved') return 'A saved key is configured. Leave blank to keep it.';
    if (provider.keySource === 'environment') return 'Using an environment key. Entering a key creates a saved override.';
    if (provider.supportsKeyless) return 'This provider supports keyless local access.';
    return 'No API key is configured.';
}

function providerModelIds(provider) {
    return Array.isArray(provider?.models)
        ? provider.models
            .map((model) => String(model?.id || '').trim())
            .filter(Boolean)
        : [];
}

function openProviderEditor(provider = null) {
    const els = getSettingsElements();
    if (!els.providerForm) return;
    settingsState.editingNewProvider = !provider;
    settingsState.selectedProviderId = provider?.id || null;
    els.providerForm.hidden = false;
    els.providerFormTitle.textContent = provider
        ? `Edit ${provider.displayName || provider.id}`
        : 'Add custom provider';
    els.providerId.value = provider?.id || '';
    els.providerId.readOnly = Boolean(provider);
    els.providerName.value = provider?.displayName || '';
    els.providerKind.value = provider?.kind || 'openai_compatible';
    els.providerKind.disabled = provider?.credentialBoundaryLocked === true;
    els.providerBilling.value = provider?.billing || 'unknown';
    els.providerBaseUrl.value = provider?.baseUrl || '';
    els.providerBaseUrl.readOnly = provider?.credentialBoundaryLocked === true;
    els.providerCapabilities.value = Array.isArray(provider?.capabilities)
        ? provider.capabilities.join(', ')
        : 'text, tools';
    settingsState.originalProviderModelIds = providerModelIds(provider);
    els.providerModels.value = settingsState.originalProviderModelIds.join('\n');
    els.providerApiKey.value = '';
    els.providerApiKey.disabled = provider?.credentialBoundaryLocked === true;
    els.providerKeyStatus.textContent = providerKeyStatus(provider);
    els.providerEnabled.checked = provider?.enabled !== false;
    els.providerClearKey.checked = false;
    els.providerClearKey.disabled = provider?.credentialBoundaryLocked === true || provider?.keySource !== 'saved';
    els.providerDelete.disabled = !provider?.isSaved;
    els.providerTest.disabled = !provider?.configured;
    requestAnimationFrame(() => els.providerForm.scrollIntoView({ behavior: 'smooth', block: 'nearest' }));
}

function closeProviderEditor() {
    const els = getSettingsElements();
    settingsState.selectedProviderId = null;
    settingsState.editingNewProvider = false;
    settingsState.originalProviderModelIds = [];
    if (els.providerForm) els.providerForm.hidden = true;
    if (els.providerApiKey) els.providerApiKey.value = '';
}

function parseProviderModelIds(value) {
    const modelIds = [...new Set(
        String(value || '')
            .split(/\r?\n/)
            .map((item) => item.trim())
            .filter(Boolean),
    )];
    if (modelIds.length > 500) {
        throw new Error('A provider can contain at most 500 manual model IDs.');
    }
    const invalid = modelIds.find((modelId) => modelId.length > 256);
    if (invalid) {
        throw new Error(`Model ID exceeds 256 characters: ${invalid.slice(0, 40)}`);
    }
    return modelIds;
}

function manualProviderModels(modelIds, billing, capabilities) {
    const capabilitySet = new Set(capabilities);
    const inputModalities = ['text'];
    if (['image', 'images', 'vision'].some((item) => capabilitySet.has(item))) {
        inputModalities.push('image');
    }
    if (['file', 'files', 'pdf'].some((item) => capabilitySet.has(item))) {
        inputModalities.push('file');
    }
    return modelIds.map((modelId) => ({
        id: modelId,
        label: modelId,
        billing,
        capabilities: [...capabilities],
        inputModalities: [...inputModalities],
    }));
}

function providerFormPayload() {
    const els = getSettingsElements();
    const providerId = els.providerId.value.trim().toLowerCase();
    if (!/^[a-z0-9][a-z0-9._-]{0,63}$/.test(providerId)) {
        throw new Error('Provider ID must use lowercase letters, numbers, dots, underscores, or hyphens.');
    }
    const displayName = els.providerName.value.trim();
    if (!displayName) throw new Error('Display name is required.');
    const baseUrl = settingsProviderBaseUrl(els.providerBaseUrl.value);
    const capabilities = [...new Set(
        els.providerCapabilities.value
            .split(',')
            .map((item) => item.trim().toLowerCase())
            .filter(Boolean),
    )];
    const apiKey = els.providerApiKey.value.trim();
    const clearApiKey = els.providerClearKey.checked;
    if (apiKey && clearApiKey) {
        throw new Error('Choose either a replacement API key or remove the saved key, not both.');
    }
    const payload = {
        displayName,
        kind: els.providerKind.value || 'openai_compatible',
        baseUrl,
        clearApiKey,
        enabled: els.providerEnabled.checked,
        billing: els.providerBilling.value || 'unknown',
        capabilities: capabilities.length ? capabilities : ['text'],
    };
    const modelIds = parseProviderModelIds(els.providerModels.value);
    const originalIds = settingsState.originalProviderModelIds;
    const modelsChanged = settingsState.editingNewProvider
        || modelIds.length !== originalIds.length
        || modelIds.some((modelId, index) => modelId !== originalIds[index]);
    if (modelsChanged) {
        payload.models = manualProviderModels(
            modelIds,
            payload.billing,
            payload.capabilities,
        );
    }
    if (apiKey) payload.apiKey = apiKey;
    return { providerId, payload };
}

async function saveProvider(event) {
    event?.preventDefault();
    const els = getSettingsElements();
    try {
        const { providerId, payload } = providerFormPayload();
        els.providerSave.disabled = true;
        setProviderStatus(`Saving ${providerId}...`);
        const { response, data } = await fetchJson(`/chat/providers/${encodeURIComponent(providerId)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!response.ok || data?.ok !== true || !data?.provider) {
            throw new Error(settingsApiError(response, data, 'Could not save provider'));
        }
        await loadProviders();
        const saved = settingsState.providers.find((provider) => provider.id === providerId);
        openProviderEditor(saved || data.provider);
        setProviderStatus(`${saved?.displayName || providerId} saved.`, 'success');
    } catch (error) {
        setProviderStatus(error?.message || 'Could not save provider.', 'error');
    } finally {
        if (els.providerSave) els.providerSave.disabled = false;
    }
}

async function testProvider(providerId = settingsState.selectedProviderId) {
    if (!providerId) {
        setProviderStatus('Save the provider before testing it.', 'error');
        return;
    }
    const els = getSettingsElements();
    if (els.providerTest && settingsState.selectedProviderId === providerId) {
        els.providerTest.disabled = true;
    }
    setProviderStatus(`Testing ${providerId} using the saved configuration...`);
    try {
        const { response, data } = await fetchJson(`/chat/providers/${encodeURIComponent(providerId)}/test`, {
            method: 'POST',
        });
        if (!response.ok || data?.ok !== true) {
            throw new Error(settingsApiError(response, data, 'Provider connection test failed'));
        }
        await loadProviders();
        const provider = settingsState.providers.find((item) => item.id === providerId);
        if (settingsState.selectedProviderId === providerId && provider) openProviderEditor(provider);
        const liveVerified = data?.verified === true
            && data.verificationMode === 'live'
            && data.status === 'connected';
        if (liveVerified) {
            setProviderStatus(
                `${provider?.displayName || providerId} connected. ${data.modelCount || 0} models discovered.`,
                'success',
            );
        } else {
            const catalogOnlyMessage = 'Configuration saved; live connection not verified.';
            setProviderStatus(
                `${catalogOnlyMessage} ${data.modelCount || 0} catalog models available.`,
            );
        }
    } catch (error) {
        setProviderStatus(error?.message || 'Provider connection test failed.', 'error');
    } finally {
        const provider = settingsState.providers.find((item) => item.id === providerId);
        if (els.providerTest && settingsState.selectedProviderId === providerId) {
            els.providerTest.disabled = provider?.configured !== true;
        }
    }
}

async function deleteProvider() {
    const providerId = settingsState.selectedProviderId;
    const provider = settingsState.providers.find((item) => item.id === providerId);
    if (!provider || !provider.isSaved) return;
    const prompt = provider.isPreset
        ? `Remove the saved override for ${provider.displayName}? The built-in preset will remain.`
        : `Remove the saved provider ${provider.displayName}?`;
    if (!confirm(prompt)) return;
    const els = getSettingsElements();
    els.providerDelete.disabled = true;
    setProviderStatus(`Removing saved configuration for ${provider.id}...`);
    try {
        const { response, data } = await fetchJson(`/chat/providers/${encodeURIComponent(provider.id)}`, {
            method: 'DELETE',
        });
        if (!response.ok || data?.ok !== true) {
            throw new Error(settingsApiError(response, data, 'Could not remove provider'));
        }
        closeProviderEditor();
        await loadProviders();
        setProviderStatus(`${provider.displayName} saved configuration removed.`, 'success');
    } catch (error) {
        setProviderStatus(error?.message || 'Could not remove provider.', 'error');
        els.providerDelete.disabled = false;
    }
}

// --- Codex OAuth API Keys ---

const CODEX_ACCESS_KEYS_ENDPOINT = '/chat/providers/codex-oauth/access-keys';

function setCodexKeyStatus(message = '', variant = '') {
    const { codexKeyStatus } = getSettingsElements();
    if (!codexKeyStatus) return;
    codexKeyStatus.textContent = message;
    codexKeyStatus.classList.remove('error', 'success');
    if (variant === 'error' || variant === 'success') {
        codexKeyStatus.classList.add(variant);
    }
}

function clearRevealedCodexKey() {
    const { codexKeyCreated, codexKeyValue } = getSettingsElements();
    if (codexKeyValue) codexKeyValue.value = '';
    if (codexKeyCreated) codexKeyCreated.hidden = true;
}

function formatCodexKeyTime(value) {
    if (!value) return 'Never';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
}

function renderCodexAccessKeys() {
    const { codexKeyList } = getSettingsElements();
    if (!codexKeyList) return;
    codexKeyList.replaceChildren();

    if (!settingsState.codexAccessKeys.length) {
        const empty = document.createElement('p');
        empty.className = 'settings-key-status';
        empty.textContent = 'No Codex OAuth API keys have been created.';
        codexKeyList.appendChild(empty);
        return;
    }

    settingsState.codexAccessKeys.forEach((record) => {
        const row = document.createElement('div');
        row.className = 'settings-codex-key-row';

        const details = document.createElement('div');
        details.className = 'settings-codex-key-meta';

        const heading = document.createElement('div');
        heading.className = 'settings-codex-key-heading';
        const name = document.createElement('strong');
        name.textContent = record.name || record.id;
        const state = document.createElement('span');
        state.className = 'settings-codex-key-state is-' + (record.status || 'unknown');
        state.textContent = record.status || 'unknown';
        heading.append(name, state);

        const prefix = document.createElement('code');
        prefix.textContent = String(record.prefix || '') + '...';

        const metadata = document.createElement('small');
        const scopes = Array.isArray(record.scopes) ? record.scopes.join(', ') : '';
        const expires = record.expiresAt ? formatCodexKeyTime(record.expiresAt) : 'Never';
        const lastUsed = record.lastUsedAt ? formatCodexKeyTime(record.lastUsedAt) : 'Never';
        let usageText = '';
        if (record.usage && typeof record.usage.requests === 'number') {
            usageText = ' · Requests: ' + record.usage.requests
                + (record.usage.errors ? ' (' + record.usage.errors + ' errors)' : '')
                + (record.usage.rateLimited ? ' (' + record.usage.rateLimited + ' rate-limited)' : '')
                + ' since restart';
        }
        metadata.textContent = 'Scopes: ' + scopes + ' · Expires: ' + expires + ' · Last used: ' + lastUsed + usageText;
        details.append(heading, prefix, metadata);
        row.appendChild(details);

        const actions = document.createElement('div');
        actions.className = 'settings-codex-key-actions';

        const rotate = document.createElement('button');
        rotate.className = 'btn btn-secondary';
        rotate.type = 'button';
        rotate.textContent = 'Rotate';
        rotate.title = 'Issue a new secret; the old token stops working immediately.';
        rotate.addEventListener('click', () => rotateCodexAccessKey(record));

        const revoke = document.createElement('button');
        revoke.className = 'btn btn-danger';
        revoke.type = 'button';
        revoke.textContent = 'Revoke';
        revoke.disabled = record.status !== 'active';
        revoke.addEventListener('click', () => revokeCodexAccessKey(record));

        const remove = document.createElement('button');
        remove.className = 'btn btn-danger';
        remove.type = 'button';
        remove.textContent = 'Delete';
        remove.title = 'Purge this key from the registry entirely.';
        remove.addEventListener('click', () => deleteCodexAccessKey(record));

        actions.append(rotate, revoke, remove);
        row.appendChild(actions);
        codexKeyList.appendChild(row);
    });
}

async function loadCodexAccessKeys() {
    clearRevealedCodexKey();
    const { response, data } = await fetchJson(CODEX_ACCESS_KEYS_ENDPOINT);
    if (!response.ok || data?.ok !== true || !Array.isArray(data.keys)) {
        settingsState.codexAccessKeys = [];
        settingsState.codexOAuthReady = false;
        renderCodexAccessKeys();
        throw new Error(settingsApiError(response, data, 'Could not load Codex OAuth API keys'));
    }
    settingsState.codexAccessKeys = data.keys;
    settingsState.codexOAuthReady = data.oauth?.ready === true;
    renderCodexAccessKeys();
    const { codexKeyCreate } = getSettingsElements();
    if (codexKeyCreate) codexKeyCreate.disabled = !settingsState.codexOAuthReady;
    if (!settingsState.codexOAuthReady) {
        setCodexKeyStatus(data.oauth?.detail || "Run 'codex login' before creating a key.", 'error');
    }
}

async function createCodexAccessKey() {
    const els = getSettingsElements();
    if (!settingsState.codexOAuthReady) {
        setCodexKeyStatus("Run 'codex login' before creating a key.", 'error');
        return;
    }
    const name = els.codexKeyName?.value.trim() || '';
    const scopes = [];
    if (els.codexKeyScopeModels?.checked) scopes.push('models:read');
    if (els.codexKeyScopeResponses?.checked) scopes.push('responses:write');
    if (!name) {
        setCodexKeyStatus('Enter a name for this key.', 'error');
        els.codexKeyName?.focus();
        return;
    }
    if (!scopes.length) {
        setCodexKeyStatus('Select at least one scope.', 'error');
        return;
    }

    const payload = { name, scopes };
    if (els.codexKeyExpires?.value) {
        const expiresAt = new Date(els.codexKeyExpires.value);
        if (Number.isNaN(expiresAt.getTime()) || expiresAt <= new Date()) {
            setCodexKeyStatus('Expiry must be a valid future time.', 'error');
            return;
        }
        payload.expiresAt = expiresAt.toISOString();
    }

    clearRevealedCodexKey();
    els.codexKeyCreate.disabled = true;
    setCodexKeyStatus('Creating Codex OAuth API key...');
    try {
        const { response, data } = await fetchJson(CODEX_ACCESS_KEYS_ENDPOINT, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!response.ok || data?.ok !== true || typeof data.key !== 'string') {
            throw new Error(settingsApiError(response, data, 'Could not create Codex OAuth API key'));
        }
        els.codexKeyValue.value = data.key;
        els.codexKeyCreated.hidden = false;
        if (data.record && typeof data.record === 'object') {
            settingsState.codexAccessKeys = [
                data.record,
                ...settingsState.codexAccessKeys.filter((record) => record.id !== data.record.id),
            ];
            renderCodexAccessKeys();
        }
        els.codexKeyName.value = '';
        els.codexKeyExpires.value = '';
        setCodexKeyStatus('Codex OAuth API key created.', 'success');
    } catch (error) {
        setCodexKeyStatus(error?.message || 'Could not create Codex OAuth API key.', 'error');
    } finally {
        els.codexKeyCreate.disabled = !settingsState.codexOAuthReady;
    }
}

async function copyCodexAccessKey() {
    const { codexKeyValue } = getSettingsElements();
    if (!codexKeyValue?.value) return;
    try {
        if (navigator?.clipboard?.writeText) {
            await navigator.clipboard.writeText(codexKeyValue.value);
        } else if (typeof manualClipboardFallback === 'function') {
            manualClipboardFallback(codexKeyValue);
        } else {
            throw new Error('Clipboard access is unavailable');
        }
        setCodexKeyStatus('Codex OAuth API key copied.', 'success');
    } catch (error) {
        console.warn('[SETTINGS] Clipboard write failed:', error);
        codexKeyValue.focus();
        codexKeyValue.select();
        setCodexKeyStatus('Clipboard copy failed. Copy the selected key manually.', 'error');
    }
}

function dismissCodexAccessKey() {
    clearRevealedCodexKey();
    setCodexKeyStatus('');
}

async function revokeCodexAccessKey(record) {
    const label = record?.name || record?.id;
    if (!record?.id || !confirm('Revoke ' + label + '? Existing clients using it will stop working.')) {
        return;
    }
    setCodexKeyStatus('Revoking ' + label + '...');
    try {
        const endpoint = CODEX_ACCESS_KEYS_ENDPOINT + '/' + encodeURIComponent(record.id) + '/revoke';
        const { response, data } = await fetchJson(endpoint, { method: 'POST' });
        if (!response.ok || data?.ok !== true || !data.record) {
            throw new Error(settingsApiError(response, data, 'Could not revoke Codex OAuth API key'));
        }
        await loadCodexAccessKeys();
        setCodexKeyStatus(label + ' revoked.', 'success');
    } catch (error) {
        setCodexKeyStatus(error?.message || 'Could not revoke Codex OAuth API key.', 'error');
    }
}

async function rotateCodexAccessKey(record) {
    const label = record?.name || record?.id;
    if (!record?.id || !confirm('Rotate ' + label + '? The current token stops working immediately and a new one is shown once.')) {
        return;
    }
    const els = getSettingsElements();
    clearRevealedCodexKey();
    setCodexKeyStatus('Rotating ' + label + '...');
    try {
        const endpoint = CODEX_ACCESS_KEYS_ENDPOINT + '/' + encodeURIComponent(record.id) + '/rotate';
        const { response, data } = await fetchJson(endpoint, { method: 'POST' });
        if (!response.ok || data?.ok !== true || typeof data.key !== 'string') {
            throw new Error(settingsApiError(response, data, 'Could not rotate Codex OAuth API key'));
        }
        if (els.codexKeyValue) els.codexKeyValue.value = data.key;
        if (els.codexKeyCreated) els.codexKeyCreated.hidden = false;
        await loadCodexAccessKeys();
        // loadCodexAccessKeys clears the revealed key; restore the rotated secret.
        if (els.codexKeyValue) els.codexKeyValue.value = data.key;
        if (els.codexKeyCreated) els.codexKeyCreated.hidden = false;
        setCodexKeyStatus(label + ' rotated. Copy the new key now.', 'success');
    } catch (error) {
        setCodexKeyStatus(error?.message || 'Could not rotate Codex OAuth API key.', 'error');
    }
}

async function lockdownCodexAccessKeys() {
    if (!confirm('Lockdown: revoke ALL active keys and require a key for every /v1 request? Existing clients stop working immediately. This cannot be undone.')) {
        return;
    }
    setCodexKeyStatus('Locking down...');
    try {
        const { response, data } = await fetchJson(CODEX_ACCESS_KEYS_ENDPOINT + '/lockdown', { method: 'POST' });
        if (!response.ok || data?.ok !== true) {
            throw new Error(settingsApiError(response, data, 'Could not lock down keys'));
        }
        await loadCodexAccessKeys();
        setCodexKeyStatus('Locked down. ' + (data.revoked || 0) + ' key(s) revoked; /v1 now requires a key.', 'success');
    } catch (error) {
        setCodexKeyStatus(error?.message || 'Could not lock down keys.', 'error');
    }
}

async function deleteCodexAccessKey(record) {
    const label = record?.name || record?.id;
    if (!record?.id || !confirm('Delete ' + label + ' permanently? This purges the key record and cannot be undone.')) {
        return;
    }
    setCodexKeyStatus('Deleting ' + label + '...');
    try {
        const endpoint = CODEX_ACCESS_KEYS_ENDPOINT + '/' + encodeURIComponent(record.id);
        const { response, data } = await fetchJson(endpoint, { method: 'DELETE' });
        if (!response.ok || data?.ok !== true) {
            throw new Error(settingsApiError(response, data, 'Could not delete Codex OAuth API key'));
        }
        await loadCodexAccessKeys();
        setCodexKeyStatus(label + ' deleted.', 'success');
    } catch (error) {
        setCodexKeyStatus(error?.message || 'Could not delete Codex OAuth API key.', 'error');
    }
}

// --- Chat Defaults ---

function setChatDefaultsStatus(message = '', variant = '') {
    const { systemPromptStatus } = getSettingsElements();
    if (!systemPromptStatus) return;
    systemPromptStatus.textContent = message;
    systemPromptStatus.classList.remove('error', 'success');
    if (variant === 'error' || variant === 'success') {
        systemPromptStatus.classList.add(variant);
    }
}

function settingsOptionalNumber(input, integer = false) {
    const raw = input?.value?.trim();
    if (!raw) return null;
    const value = integer ? Number.parseInt(raw, 10) : Number.parseFloat(raw);
    return Number.isFinite(value) ? value : null;
}

function renderChatDefaults() {
    const els = getSettingsElements();
    const settings = settingsState.chatSettings;
    if (!settings) return;
    els.systemPromptInput.value = settings.defaultSystemPrompt || '';
    els.chatTemperature.value = settings.temperature ?? '';
    els.chatMaxOutput.value = settings.maxOutputTokens ?? '';
    els.chatMaxRounds.value = settings.maxToolRounds ?? 8;
    els.chatReasoning.checked = settings.includeReasoning !== false;
    els.chatReasoningEffort.value = settings.reasoningEffort || '';
    els.chatReasoningEffort.disabled = !els.chatReasoning.checked;
    els.chatManagementTools.checked = settings.includeManagementTools === true;
}

async function loadChatDefaults() {
    const { response, data } = await fetchJson('/chat/sessions/settings');
    if (!response.ok || data?.ok !== true || !data?.settings) {
        throw new Error(settingsApiError(response, data, 'Could not load chat defaults'));
    }
    settingsState.chatSettings = data.settings;
    renderChatDefaults();
}

async function saveChatDefaults() {
    const els = getSettingsElements();
    const temperature = settingsOptionalNumber(els.chatTemperature);
    const maxOutputTokens = settingsOptionalNumber(els.chatMaxOutput, true);
    const maxToolRounds = settingsOptionalNumber(els.chatMaxRounds, true);
    if (temperature !== null && (temperature < 0 || temperature > 2)) {
        setChatDefaultsStatus('Temperature must be between 0 and 2.', 'error');
        return;
    }
    if (maxOutputTokens !== null && maxOutputTokens < 1) {
        setChatDefaultsStatus('Max output tokens must be at least 1.', 'error');
        return;
    }
    if (!maxToolRounds || maxToolRounds < 1 || maxToolRounds > 20) {
        setChatDefaultsStatus('Max tool rounds must be between 1 and 20.', 'error');
        return;
    }
    const payload = {
        default_system_prompt: els.systemPromptInput.value,
        temperature,
        max_output_tokens: maxOutputTokens,
        max_tool_rounds: maxToolRounds,
        include_reasoning: els.chatReasoning.checked,
        reasoning_effort: els.chatReasoning.checked
            ? (els.chatReasoningEffort.value || null)
            : null,
        include_management_tools: els.chatManagementTools.checked,
    };
    els.systemPromptSave.disabled = true;
    setChatDefaultsStatus('Saving chat defaults...');
    try {
        const { response, data } = await fetchJson('/chat/sessions/settings', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!response.ok || data?.ok !== true || !data?.settings) {
            throw new Error(settingsApiError(response, data, 'Could not save chat defaults'));
        }
        settingsState.chatSettings = data.settings;
        renderChatDefaults();
        setChatDefaultsStatus('Chat defaults saved.', 'success');
    } catch (error) {
        setChatDefaultsStatus(error?.message || 'Could not save chat defaults.', 'error');
    } finally {
        els.systemPromptSave.disabled = false;
    }
}

// --- Clear Storage ---

function clearLocalStorage() {
    if (!confirm('Clear all local preferences? This removes your API key, theme, chat history, and page state.')) {
        return;
    }
    localStorage.clear();
    location.reload();
}

// --- Bind & Init ---

function bindSettingsHandlers() {
    const els = getSettingsElements();
    if (els.codeModeToggle) {
        els.codeModeToggle.addEventListener('click', toggleCodeMode);
    }
    if (els.apiKeySave) {
        els.apiKeySave.addEventListener('click', saveApiKey);
    }
    if (els.apiKeyReveal) {
        els.apiKeyReveal.addEventListener('click', toggleApiKeyReveal);
    }
    if (els.apiKeyInput) {
        els.apiKeyInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                saveApiKey();
            }
        });
    }
    if (els.themeBtn) {
        els.themeBtn.addEventListener('click', () => {
            if (typeof toggleTheme === 'function') toggleTheme();
        });
    }
    if (els.clearStorageBtn) {
        els.clearStorageBtn.addEventListener('click', clearLocalStorage);
    }
    els.providerAdd?.addEventListener('click', () => openProviderEditor());
    els.providerCancel?.addEventListener('click', closeProviderEditor);
    els.providerForm?.addEventListener('submit', saveProvider);
    els.providerTest?.addEventListener('click', () => testProvider());
    els.providerDelete?.addEventListener('click', deleteProvider);
    els.providerApiKey?.addEventListener('input', () => {
        if (els.providerApiKey.value && els.providerClearKey) {
            els.providerClearKey.checked = false;
        }
    });
    els.providerClearKey?.addEventListener('change', () => {
        if (els.providerClearKey.checked && els.providerApiKey) {
            els.providerApiKey.value = '';
        }
    });
    els.codexKeyCreate?.addEventListener('click', createCodexAccessKey);
    els.codexKeyCopy?.addEventListener('click', copyCodexAccessKey);
    els.codexKeyDismiss?.addEventListener('click', dismissCodexAccessKey);
    els.codexKeyLockdown?.addEventListener('click', lockdownCodexAccessKeys);
    els.codexKeyName?.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            event.preventDefault();
            createCodexAccessKey();
        }
    });
    els.systemPromptSave?.addEventListener('click', saveChatDefaults);
    els.chatReasoning?.addEventListener('change', () => {
        if (els.chatReasoningEffort) {
            els.chatReasoningEffort.disabled = !els.chatReasoning.checked;
        }
    });
}

async function refreshSettingsPage() {
    loadApiKeyState();
    await Promise.all([
        loadCodeModeState(),
        loadProviders().catch((error) => {
            setProviderStatus(error?.message || 'Could not load model providers.', 'error');
        }),
        loadCodexAccessKeys().catch((error) => {
            setCodexKeyStatus(error?.message || 'Could not load Codex OAuth API keys.', 'error');
        }),
        loadChatDefaults().catch((error) => {
            setChatDefaultsStatus(error?.message || 'Could not load chat defaults.', 'error');
        }),
    ]);
}

async function initSettingsPage() {
    if (!settingsState.initialized) {
        bindSettingsHandlers();
        settingsState.initialized = true;
    }
    await refreshSettingsPage();
}

window.initSettingsPage = initSettingsPage;
