/* MCPO Configuration Page Functions */

// Additional config button functions
function validateConfig() {
    const editor = document.getElementById('server-config-editor');
    if (!editor) return;
    
    try {
        JSON.parse(editor.value);
        alert('Configuration is valid JSON');
    } catch (e) {
        alert('Invalid JSON: ' + e.message);
    }
}

async function resetConfig() {
    if (confirm('Reset configuration to last saved version?')) {
        await loadConfigContent();
    }
}

// Expose for inline handlers
window.validateConfig = validateConfig;
window.resetConfig = resetConfig;

const CLIENT_CONFIG = {
    tabId: 'config-client-tab',
    textareaId: 'client-config-output',
    statusId: 'client-config-status',
    refreshBtnId: 'client-config-refresh-btn',
    copyBtnId: 'client-config-copy-btn',
};

const CLIENT_STATUS_VARIANTS = ['info', 'success', 'warning', 'error'];

const clientConfigState = {
    initialized: false,
    dirty: true,
    proxyBaseUrl: null,
    aggregatePath: null,
    lastRenderedHash: null,
    lastUpdated: null,
    latestStates: null,
    elements: null,
    fallbackPort: 8001,
};

function initConfigTabs() {
    const container = document.getElementById('config-tabs');
    if (!container) return;

    const tabButtons = Array.from(container.querySelectorAll('.tab'));
    const tabPanels = Array.from(container.querySelectorAll('.tab-content'));

    tabButtons.forEach((button) => {
        button.addEventListener('click', () => {
            const targetId = button.getAttribute('data-tab-target');
            if (!targetId) return;

            tabButtons.forEach((btn) => {
                const isActive = btn === button;
                btn.classList.toggle('active', isActive);
                btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
            });

            tabPanels.forEach((panel) => {
                const isActive = panel.id === targetId;
                panel.classList.toggle('active', isActive);
                if (isActive) {
                    panel.removeAttribute('aria-hidden');
                } else {
                    panel.setAttribute('aria-hidden', 'true');
                }
            });

            if (targetId === CLIENT_CONFIG.tabId) {
                onClientConfigTabActivated();
            }
        });
    });
}

document.addEventListener('DOMContentLoaded', initConfigTabs);

document.addEventListener('mcpo:server-states-updated', (event) => {
    if (!event || !event.detail) return;
    const snapshot = event.detail.serverStates;
    if (snapshot && typeof snapshot === 'object') {
        clientConfigState.latestStates = snapshot;
        clientConfigState.dirty = true;
        if (clientConfigState.initialized && isClientConfigTabActive()) {
            renderClientConfigPanel().catch((error) => {
                console.warn('[CONFIG] Failed to update client config panel:', error);
            });
        }
    }
});

function onClientConfigTabActivated() {
    ensureClientConfigPanel();
    renderClientConfigPanel({ force: clientConfigState.dirty }).catch((error) => {
        console.warn('[CONFIG] Failed to render client config panel:', error);
    });
}

function ensureClientConfigPanel() {
    if (clientConfigState.initialized) return;
    const elements = getClientConfigElements();
    if (!elements.textarea) {
        return;
    }
    elements.refreshBtn?.addEventListener('click', () => {
        renderClientConfigPanel({ force: true, manual: true }).catch((error) => {
            console.warn('[CONFIG] Manual refresh failed:', error);
            setClientConfigStatus('Failed to refresh configuration.', 'error');
        });
    });
    elements.copyBtn?.addEventListener('click', async () => {
        await copyClientConfigToClipboard();
    });
    clientConfigState.initialized = true;
}

function getClientConfigElements() {
    if (clientConfigState.elements) return clientConfigState.elements;
    clientConfigState.elements = {
        container: document.getElementById(CLIENT_CONFIG.tabId),
        textarea: document.getElementById(CLIENT_CONFIG.textareaId),
        status: document.getElementById(CLIENT_CONFIG.statusId),
        refreshBtn: document.getElementById(CLIENT_CONFIG.refreshBtnId),
        copyBtn: document.getElementById(CLIENT_CONFIG.copyBtnId),
    };
    return clientConfigState.elements;
}

function isClientConfigTabActive() {
    const elements = getClientConfigElements();
    return !!(elements.container && elements.container.classList.contains('active'));
}

async function renderClientConfigPanel(options = {}) {
    const { force = false } = options;
    const elements = getClientConfigElements();
    if (!elements.textarea) {
        return;
    }
    await ensureClientConfigMetadata();
    const states = clientConfigState.latestStates
        || (typeof window.getServerStatesSnapshot === 'function'
            ? window.getServerStatesSnapshot()
            : null)
        || {};
    const hasServers = Object.keys(states).length > 0;

    const payload = buildClientConfigPayload(states);
    const json = JSON.stringify(payload, null, 2);
    const hash = `${clientConfigState.proxyBaseUrl || ''}|${json}`;
    if (!force && hash === clientConfigState.lastRenderedHash) {
        return;
    }

    elements.textarea.value = json;
    clientConfigState.lastRenderedHash = hash;
    clientConfigState.lastUpdated = new Date();
    clientConfigState.dirty = false;
    const endpointCount = Object.keys(payload.mcpServers || {}).length;
    const enabledServers = Math.max(0, endpointCount - 1);
    const timestamp = clientConfigState.lastUpdated.toLocaleTimeString();
    if (hasServers) {
        const label = enabledServers === 1 ? '1 server endpoint' : `${enabledServers} server endpoints`;
        setClientConfigStatus(`Updated at ${timestamp} • Aggregate + ${label}`, 'success');
    } else {
        setClientConfigStatus(`Updated at ${timestamp} • Aggregate endpoint ready (no servers enabled)`, 'warning');
    }
}

async function copyClientConfigToClipboard() {
    const elements = getClientConfigElements();
    if (!elements.textarea) return;
    if (!elements.textarea.value || !elements.textarea.value.trim()) {
        await renderClientConfigPanel({ force: true });
    }
    const value = elements.textarea.value;
    try {
        if (navigator?.clipboard?.writeText) {
            await navigator.clipboard.writeText(value);
        } else {
            manualClipboardFallback(elements.textarea);
        }
        setClientConfigStatus('Client configuration copied to clipboard.', 'success');
    } catch (error) {
        console.warn('[CONFIG] Clipboard write failed:', error);
        setClientConfigStatus('Clipboard copy failed. Select the JSON and copy manually.', 'error');
    }
}

function manualClipboardFallback(textarea) {
    if (!textarea) {
        throw new Error('No textarea available for clipboard fallback');
    }
    textarea.focus();
    textarea.select();
    let success = false;
    try {
        success = typeof document.execCommand === 'function' && document.execCommand('copy');
    } finally {
        textarea.setSelectionRange(0, 0);
        textarea.blur();
    }
    if (!success) {
        throw new Error('Browser blocked clipboard access');
    }
}

function setClientConfigStatus(message, variant = 'info') {
    const elements = getClientConfigElements();
    if (!elements.status) return;
    const classList = elements.status.classList;
    CLIENT_STATUS_VARIANTS.forEach((v) => classList.remove(`status-${v}`));
    if (variant && CLIENT_STATUS_VARIANTS.includes(variant)) {
        classList.add(`status-${variant}`);
    }
    elements.status.textContent = message || '';
}

async function ensureClientConfigMetadata() {
    if (clientConfigState.proxyBaseUrl && clientConfigState.aggregatePath) {
        return;
    }
    try {
        const { data } = await fetchJson('/_meta/logs/sources');
        if (data && data.ok && Array.isArray(data.sources)) {
            const proxySource = data.sources.find((source) => source.id === 'mcp');
            if (proxySource) {
                applyProxySourceMetadata(proxySource);
            }
        }
    } catch (error) {
        console.warn('[CONFIG] Failed to load proxy metadata:', error);
    }
    if (!clientConfigState.proxyBaseUrl) {
        clientConfigState.proxyBaseUrl = buildDefaultProxyBaseUrl();
    }
    if (!clientConfigState.aggregatePath) {
        clientConfigState.aggregatePath = '/mcp';
    }
}

function applyProxySourceMetadata(source) {
    const baseUrl = deriveProxyBaseUrl(source);
    if (baseUrl) {
        clientConfigState.proxyBaseUrl = baseUrl;
    }
    const derivedPath = deriveProxyPath(source);
    if (derivedPath) {
        clientConfigState.aggregatePath = derivedPath;
    }
}

function deriveProxyBaseUrl(source) {
    if (source.url) {
        try {
            const parsed = new URL(source.url, window.location.href);
            return `${parsed.protocol}//${parsed.host}`;
        } catch (error) {
            console.warn('[CONFIG] Failed to parse proxy URL:', error);
        }
    }
    const host = source.host || window.location.hostname;
    const port = source.port || clientConfigState.fallbackPort;
    if (!host) return null;
    const protocol = window.location.protocol || 'http:';
    return `${protocol}//${host}${port ? `:${port}` : ''}`;
}

function deriveProxyPath(source) {
    if (source.path && typeof source.path === 'string' && source.path.trim()) {
        return normalizeProxyPath(source.path);
    }
    if (source.url) {
        try {
            const parsed = new URL(source.url, window.location.href);
            return normalizeProxyPath(parsed.pathname);
        } catch (error) {
            console.warn('[CONFIG] Failed to derive proxy path from URL:', error);
        }
    }
    return null;
}

function normalizeProxyPath(path) {
    let normalized = (path || '').trim();
    if (!normalized) return null;
    if (!normalized.startsWith('/')) normalized = `/${normalized}`;
    if (normalized.length > 1 && normalized.endsWith('/')) {
        normalized = normalized.slice(0, -1);
    }
    return normalized || '/mcp';
}

function buildDefaultProxyBaseUrl() {
    const { protocol, hostname } = window.location;
    const port = clientConfigState.fallbackPort;
    return `${protocol}//${hostname}${port ? `:${port}` : ''}`;
}

function buildClientConfigPayload(states) {
    const baseUrl = clientConfigState.proxyBaseUrl || buildDefaultProxyBaseUrl();
    const aggregatePath = clientConfigState.aggregatePath || '/mcp';
    const payload = { mcpServers: {} };
    payload.mcpServers['mcpo-proxy'] = {
        url: combineProxyUrl(baseUrl, aggregatePath),
        transport: 'streamable-http',
        enabled: true,
        scope: 'aggregate',
        description: 'Aggregate endpoint exposing all enabled servers',
    };

    const entries = Object.entries(states || {}).sort(([a], [b]) => a.localeCompare(b));
    entries.forEach(([name, state]) => {
        if (!state || !state.enabled) return;
        const path = typeof state.basePath === 'string' && state.basePath.trim() ? state.basePath : `/${name}/`;
        const url = combineProxyUrl(baseUrl, path);
        payload.mcpServers[name] = {
            url,
            transport: 'streamable-http',
            enabled: true,
            scope: 'server',
        };
    });
    return payload;
}

function combineProxyUrl(baseUrl, path) {
    if (!baseUrl) return path || '';
    const sanitizedBase = baseUrl.endsWith('/') ? baseUrl.slice(0, -1) : baseUrl;
    let normalizedPath = (path || '').trim();
    if (!normalizedPath) return sanitizedBase;
    if (!normalizedPath.startsWith('/')) normalizedPath = `/${normalizedPath}`;
    normalizedPath = normalizedPath.replace(/\\/g, '/');
    normalizedPath = normalizedPath.replace(/\/{2,}/g, '/');
    if (normalizedPath.length > 1 && normalizedPath.endsWith('/')) {
        normalizedPath = normalizedPath.slice(0, -1);
    }
    return `${sanitizedBase}${normalizedPath}`;
}
