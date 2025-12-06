/* MCPO Logs Console - Dual server view */
const LOGS_REFRESH_INTERVAL_MS = 1000;  // Faster refresh for real-time debugging
const LOGS_FETCH_LIMIT = 500;
const LOGS_MAX_LINES = 3000;
const SCROLL_STICKY_THRESHOLD = 100;  // More tolerance for auto-scroll stickiness
const DEFAULT_LOG_SOURCES = [
    { id: 'openapi', label: 'OpenAPI Server (8000)', port: 8000, available: true },
    { id: 'mcp', label: 'MCP Proxy (8001)', port: 8001, available: true },
];
const logsState = {
    initialized: false,
    sources: DEFAULT_LOG_SOURCES.map((src) => ({ ...src })),
    activeSource: 'openapi',
    autoRefresh: true,
    refreshTimer: null,
    buffers: new Map(),
    cursors: new Map(),
    manualScroll: new Set(),
    statuses: {},
    lastUpdated: {},
};
function mapSource(source) {
    if (!source || !source.id) {
        return null;
    }
    const fallback = source.id === 'mcp' ? 'MCP Proxy' : 'OpenAPI Server';
    const portSuffix = source.port ? ` (${source.port})` : '';
    return {
        id: source.id,
        label: `${source.label || fallback}${portSuffix}`,
        host: source.host || null,
        port: source.port || null,
        url: source.url || null,
        available: source.available !== false,
    };
}
async function loadLogSources(shouldRerender = false) {
    try {
        const { data } = await fetchJson('/_meta/logs/sources');
        if (data && data.ok && Array.isArray(data.sources) && data.sources.length) {
            const mapped = data.sources.map(mapSource).filter(Boolean);
            logsState.sources = mapped.length ? mapped : DEFAULT_LOG_SOURCES.map((src) => ({ ...src }));
        } else {
            logsState.sources = DEFAULT_LOG_SOURCES.map((src) => ({ ...src }));
        }
    } catch (error) {
        console.warn('[LOGS] Failed to load source metadata, using defaults:', error);
        logsState.sources = DEFAULT_LOG_SOURCES.map((src) => ({ ...src }));
    }
    if (!logsState.sources.some((s) => s.id === logsState.activeSource)) {
        logsState.activeSource = logsState.sources[0].id;
    }
    if (shouldRerender) {
        renderLogTabs();
        renderLogOutputs();
        logsState.sources.forEach((source) => {
            if (logsState.buffers.has(source.id)) {
                updateOutput(source.id, logsState.buffers.get(source.id) || [], { reset: true, forceScroll: true });
            }
        });
        setActiveLogSource(logsState.activeSource);
    }
}
function renderLogTabs() {
    const container = document.getElementById('logs-sources');
    if (!container) return;
    container.innerHTML = '';
    logsState.sources.forEach((source) => {
        const btn = document.createElement('button');
        btn.className = 'logs-source-btn';
        btn.textContent = source.label;
        btn.dataset.sourceId = source.id;
        if (source.available === false) {
            btn.classList.add('unavailable');
            btn.title = 'Source unavailable';
        }
        if (source.id === logsState.activeSource) {
            btn.classList.add('active');
        }
        btn.addEventListener('click', () => setActiveLogSource(source.id));
        container.appendChild(btn);
    });
}
function renderLogOutputs() {
    const pane = document.getElementById('logs-output-container');
    if (!pane) return;
    pane.innerHTML = '';
    logsState.sources.forEach((source) => {
        const wrapper = document.createElement('div');
        wrapper.className = 'logs-output';
        wrapper.dataset.sourceId = source.id;
        if (source.id === logsState.activeSource) {
            wrapper.classList.add('active');
        }

        const lines = document.createElement('div');
        lines.className = 'log-lines';
        wrapper.appendChild(lines);

        const placeholder = document.createElement('div');
        placeholder.className = 'logs-placeholder';
        placeholder.textContent = 'Loading logs...';
        lines.appendChild(placeholder);

        wrapper.addEventListener('scroll', () => handleManualScroll(source.id, wrapper));
        pane.appendChild(wrapper);
    });
}
function getOutputElement(sourceId) {
    return document.querySelector(`.logs-output[data-source-id="${sourceId}"]`);
}
function getLinesContainer(outputEl) {
    return outputEl ? outputEl.querySelector('.log-lines') : null;
}
function isNearBottom(element) {
    if (!element) return true;
    const distance = element.scrollHeight - (element.scrollTop + element.clientHeight);
    return distance <= SCROLL_STICKY_THRESHOLD;
}
function handleManualScroll(sourceId, element) {
    if (!element) return;
    if (isNearBottom(element)) {
        logsState.manualScroll.delete(sourceId);
    } else {
        logsState.manualScroll.add(sourceId);
    }
}
function showPlaceholder(sourceId, message) {
    const output = getOutputElement(sourceId);
    const container = getLinesContainer(output);
    if (!container) return;
    container.innerHTML = '';
    const placeholder = document.createElement('div');
    placeholder.className = 'logs-placeholder';
    placeholder.textContent = message;
    container.appendChild(placeholder);
}
function setActiveLogSource(sourceId) {
    if (!logsState.sources.some((s) => s.id === sourceId)) {
        return;
    }
    logsState.activeSource = sourceId;
    logsState.manualScroll.delete(sourceId);
    document.querySelectorAll('.logs-source-btn').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.sourceId === sourceId);
    });
    document.querySelectorAll('.logs-output').forEach((el) => {
        el.classList.toggle('active', el.dataset.sourceId === sourceId);
    });
    updateStatusBar();
    fetchLogsForSource(sourceId, { immediate: true });
}
function normalizeLogEntry(entry) {
    if (!entry || typeof entry !== 'object') {
        return null;
    }
    const rawLevel = entry.level || 'INFO';
    const level = typeof rawLevel === 'string' ? rawLevel.toUpperCase() : String(rawLevel).toUpperCase();
    return {
        sequence: typeof entry.sequence === 'number' ? entry.sequence : null,
        timestamp: entry.timestamp || '',
        level,
        logger: entry.logger || entry.category || '',
        category: entry.category || 'system',
        message:
            typeof entry.message === 'string'
                ? entry.message
                : entry.message != null
                ? JSON.stringify(entry.message)
                : '',
        source: entry.source || '',
    };
}
function renderLogEntry(entry) {
    const normalized = normalizeLogEntry(entry);
    if (!normalized) return null;
    const line = document.createElement('div');
    line.className = 'log-line';
    if (normalized.level) {
        line.classList.add(`level-${normalized.level.toLowerCase()}`);
    }
    if (normalized.category) {
        line.dataset.category = normalized.category;
    }
    if (normalized.sequence != null) {
        line.dataset.sequence = normalized.sequence;
    }

    const timestamp = document.createElement('span');
    timestamp.className = 'log-timestamp';
    timestamp.textContent = normalized.timestamp ? `[${normalized.timestamp}]` : '[--:--:--]';
    line.appendChild(timestamp);

    const level = document.createElement('span');
    level.className = 'log-level';
    level.textContent = normalized.level;
    line.appendChild(level);

    // Always show logger/category for better debugging context
    const loggerLabel = document.createElement('span');
    loggerLabel.className = 'log-logger';
    loggerLabel.textContent = normalized.logger || normalized.category || '-';
    line.appendChild(loggerLabel);

    const message = document.createElement('span');
    message.className = 'log-message';
    message.textContent = normalized.message;
    line.appendChild(message);

    return line;
}
function updateOutput(sourceId, entries, options = {}) {
    const output = getOutputElement(sourceId);
    const container = getLinesContainer(output);
    if (!output || !container) return;

    const shouldReset = options.reset === true;
    const buffer = shouldReset ? [] : logsState.buffers.get(sourceId)?.slice() || [];
    
    // Capture scroll position BEFORE adding new content
    const wasAtBottom = isNearBottom(output);
    const wantsAutoStick =
        options.forceScroll === true ||
        shouldReset ||
        (!logsState.manualScroll.has(sourceId) && wasAtBottom);

    if (shouldReset) {
        container.innerHTML = '';
        // Clear manual scroll flag on reset to ensure fresh auto-scroll
        logsState.manualScroll.delete(sourceId);
    }

    const fragment = document.createDocumentFragment();
    entries.forEach((entry) => {
        const normalized = normalizeLogEntry(entry);
        if (!normalized) return;
        const rendered = renderLogEntry(normalized);
        if (!rendered) return;
        fragment.appendChild(rendered);
        buffer.push(normalized);
    });

    if (fragment.childNodes.length) {
        const existingPlaceholder = container.querySelector('.logs-placeholder');
        if (existingPlaceholder) {
            existingPlaceholder.remove();
        }
        container.appendChild(fragment);
    }

    while (buffer.length > LOGS_MAX_LINES) {
        buffer.shift();
        if (container.firstChild) {
            container.removeChild(container.firstChild);
        }
    }

    if (!buffer.length) {
        showPlaceholder(sourceId, '[no log entries yet]');
    }

    logsState.buffers.set(sourceId, buffer);

    if (wantsAutoStick) {
        requestAnimationFrame(() => {
            output.scrollTop = output.scrollHeight;
            // After programmatic scroll, clear manual scroll flag so auto-stick continues
            logsState.manualScroll.delete(sourceId);
        });
    }
}
function updateStatusBar() {
    const statusEl = document.getElementById('logs-connection-status');
    const updatedEl = document.getElementById('logs-updated-label');
    if (!statusEl || !updatedEl) return;
    const status = logsState.statuses[logsState.activeSource];
    const last = logsState.lastUpdated[logsState.activeSource];
    if (status && status.ok) {
        statusEl.textContent = 'Connected';
        statusEl.className = 'status-badge active';
        statusEl.title = '';
    } else if (status && status.message) {
        statusEl.textContent = 'Unavailable';
        statusEl.className = 'status-badge';
        statusEl.title = status.message;
    } else {
        statusEl.textContent = 'Idle';
        statusEl.className = 'status-badge';
        statusEl.title = '';
    }
    if (last) {
        updatedEl.textContent = `Updated at ${last.toLocaleTimeString()}`;
    } else {
        updatedEl.textContent = 'Not yet refreshed';
    }
}
function applyAutoRefreshLabel() {
    const btn = document.getElementById('logs-auto-refresh-btn');
    if (!btn) return;
    if (logsState.autoRefresh) {
        btn.textContent = 'Auto Refresh: ON';
        btn.className = 'btn btn-primary';
    } else {
        btn.textContent = 'Auto Refresh: OFF';
        btn.className = 'btn';
    }
}
function stopLogsAutoRefresh() {
    if (logsState.refreshTimer) {
        clearInterval(logsState.refreshTimer);
        logsState.refreshTimer = null;
    }
}
function startLogsAutoRefresh() {
    stopLogsAutoRefresh();
    if (!logsState.autoRefresh) return;
    logsState.refreshTimer = setInterval(() => {
        refreshLogs().catch((error) => console.warn('[LOGS] Auto refresh failed:', error));
    }, LOGS_REFRESH_INTERVAL_MS);
}
function toggleAutoRefresh() {
    logsState.autoRefresh = !logsState.autoRefresh;
    applyAutoRefreshLabel();
    if (logsState.autoRefresh) {
        startLogsAutoRefresh();
    } else {
        stopLogsAutoRefresh();
    }
}
async function ensureLogsInitialized() {
    if (logsState.initialized) return;
    renderLogTabs();
    renderLogOutputs();
    logsState.initialized = true;
    loadLogSources(true).catch((error) => {
        console.warn('[LOGS] Failed to refresh sources:', error);
    });
}
async function updateLogs() {
    await ensureLogsInitialized();
    await refreshLogs();
}
async function refreshLogs() {
    await ensureLogsInitialized();
    if (!logsState.sources.length) return;
    await fetchLogsForSource(logsState.activeSource);
    const otherSources = logsState.sources.filter(
        (source) => source.id !== logsState.activeSource && source.available !== false
    );
    for (const source of otherSources) {
        fetchLogsForSource(source.id).catch((error) =>
            console.warn(`[LOGS] Background refresh failed for ${source.id}:`, error)
        );
    }
}
function buildSourceQuery(sourceId) {
    return sourceId;
}
async function fetchLogsForSource(sourceId, options = {}) {
    const output = getOutputElement(sourceId);
    if (!output) return;
    if (options.immediate) {
        showPlaceholder(sourceId, 'Loading logs...');
    }
    const params = new URLSearchParams();
    params.set('source', buildSourceQuery(sourceId));
    params.set('limit', String(LOGS_FETCH_LIMIT));
    
    // Determine if this is a reset/initial fetch
    const shouldReset = options.reset === true || options.immediate;
    const cursor = logsState.cursors.get(sourceId);
    
    // Only use cursor if NOT resetting
    if (!shouldReset && cursor != null) {
        params.set('cursor', String(cursor));
    }
    
    try {
        const { data } = await fetchJson(`/_meta/logs?${params.toString()}`);
        if (!data || !data.ok) {
            const message = data && data.error ? data.error.message || data.error : 'Unable to fetch logs';
            logsState.statuses[sourceId] = { ok: false, message };
            showPlaceholder(sourceId, message);
            if (sourceId === logsState.activeSource) {
                updateStatusBar();
            }
            return;
        }
        const entries = Array.isArray(data.logs) ? data.logs : [];
        if (shouldReset || cursor == null) {
            updateOutput(sourceId, entries, { reset: true, forceScroll: true });
        } else if (entries.length) {
            updateOutput(sourceId, entries);
        }
        const nextCursor =
            data.nextCursor != null
                ? data.nextCursor
                : data.latestCursor != null
                ? data.latestCursor
                : cursor ?? null;
        if (nextCursor != null) {
            logsState.cursors.set(sourceId, nextCursor);
        }
        logsState.statuses[sourceId] = { ok: true };
        logsState.lastUpdated[sourceId] = new Date();
        if (sourceId === logsState.activeSource) {
            updateStatusBar();
        }
    } catch (error) {
        logsState.statuses[sourceId] = { ok: false, message: error.message };
        showPlaceholder(sourceId, error.message || 'Failed to load logs');
        if (sourceId === logsState.activeSource) {
            updateStatusBar();
        }
    }
}
async function clearLogsForSource(sourceId) {
    const params = new URLSearchParams();
    params.set('source', buildSourceQuery(sourceId));
    const endpoint = '/_meta/logs/clear/all';
    const { data } = await fetchJson(`${endpoint}?${params.toString()}`, { method: 'POST' });
    if (!data || !data.ok) {
        throw new Error(data && data.error ? data.error : 'Failed to clear logs');
    }
    logsState.buffers.set(sourceId, []);
    logsState.cursors.delete(sourceId);
    updateOutput(sourceId, [], { reset: true });
    logsState.lastUpdated[sourceId] = new Date();
    logsState.statuses[sourceId] = { ok: true };
    if (sourceId === logsState.activeSource) {
        updateStatusBar();
    }
}
async function clearCurrentCategory() {
    await ensureLogsInitialized();
    const sourceId = logsState.activeSource;
    if (!confirm(`Clear all logs for ${sourceId.toUpperCase()}?`)) return;
    try {
        await clearLogsForSource(sourceId);
    } catch (error) {
        alert(error.message);
    }
}
async function clearAllLogs() {
    await ensureLogsInitialized();
    if (!confirm('Clear logs for all sources?')) return;
    for (const source of logsState.sources) {
        try {
            await clearLogsForSource(source.id);
        } catch (error) {
            console.error('[LOGS] Failed to clear source', source.id, error);
        }
    }
}
function clearLogs() {
    clearCurrentCategory();
}
function switchLogsCategory(nextSource) {
    if (nextSource) {
        setActiveLogSource(nextSource);
    }
}
// Expose to global scope for existing handlers
window.toggleAutoRefresh = toggleAutoRefresh;
window.refreshLogs = refreshLogs;
window.clearCurrentCategory = clearCurrentCategory;
window.clearAllLogs = clearAllLogs;
window.updateLogs = updateLogs;
window.switchLogsCategory = switchLogsCategory;
window.startLogsAutoRefresh = startLogsAutoRefresh;
window.stopLogsAutoRefresh = stopLogsAutoRefresh;
// Ensure auto-refresh button reflects state on load
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', applyAutoRefreshLabel);
} else {
    applyAutoRefreshLabel();
}
async function initLogsUI() {
    try {
        await ensureLogsInitialized();
        await refreshLogs();
        // Start auto-refresh if enabled
        if (logsState.autoRefresh) {
            startLogsAutoRefresh();
        }
    } catch (error) {
        console.error('[LOGS] Initialization failure:', error);
    }
}
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initLogsUI);
} else {
    initLogsUI();
}
