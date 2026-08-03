/* MCPO Global REST Tools Toggle (top bar) */

const restToolsState = {
    enabled: true,
    loaded: false,
};

function getRestToolsElements() {
    return {
        toggle: document.getElementById('rest-tools-toggle'),
        state: document.getElementById('rest-tools-toggle-state'),
    };
}

function renderRestToolsToggle() {
    const els = getRestToolsElements();
    if (els.toggle) {
        els.toggle.classList.toggle('on', restToolsState.enabled);
    }
    if (els.state) {
        els.state.textContent = restToolsState.enabled ? 'ON' : 'OFF';
        els.state.classList.toggle('on', restToolsState.enabled);
    }
}

async function loadRestToolsState() {
    try {
        const { data } = await fetchJson('/_meta/rest-tools');
        if (data && data.ok !== undefined) {
            restToolsState.enabled = !!data.enabled;
        }
    } catch (e) {
        console.warn('[REST-TOOLS] Failed to load state:', e);
    }
    restToolsState.loaded = true;
    renderRestToolsToggle();
}

async function toggleRestTools() {
    if (!restToolsState.loaded) return;
    const newState = !restToolsState.enabled;
    const endpoint = newState ? 'enable' : 'disable';

    // Optimistic UI
    restToolsState.enabled = newState;
    renderRestToolsToggle();

    try {
        const { data } = await fetchJson(`/_meta/rest-tools/${endpoint}`, { method: 'POST' });
        if (!data || !data.ok) {
            // Revert
            restToolsState.enabled = !newState;
            renderRestToolsToggle();
            console.error(`[REST-TOOLS] Failed to ${endpoint} REST tools:`, data);
        }
    } catch (e) {
        restToolsState.enabled = !newState;
        renderRestToolsToggle();
        console.error(`[REST-TOOLS] Error toggling REST tools:`, e);
    }
}

function bindRestToolsHandlers() {
    const els = getRestToolsElements();
    if (els.toggle) {
        els.toggle.addEventListener('click', toggleRestTools);
    }
}

window.loadRestToolsState = loadRestToolsState;
window.toggleRestTools = toggleRestTools;
window.bindRestToolsHandlers = bindRestToolsHandlers;
