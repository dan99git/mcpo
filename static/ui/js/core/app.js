/* MCPO Main Application Logic - Initialization & Polling */

let isPolling = false;

function sanitizeLegacyState() {
    try {
        localStorage.removeItem('mcpo-agents');
    } catch (error) {
        console.warn('[APP] Failed to clear legacy agents state:', error);
    }

    // Do not remap legacy pages; we want zero traces and no implicit redirects.
}

// Polling for real-time updates
function startPolling() {
    if (isPolling) return;
    isPolling = true;
    
    // Update server states every 5 seconds
    setInterval(async () => {
        await updateServerStates();
    }, 5000);
    
    // Start enhanced logs auto-refresh
    startLogsAutoRefresh();
}

// Initialize with Persistence
document.addEventListener('DOMContentLoaded', async () => {
    console.log('[APP] DOM loaded, starting initialization...');
    
    // Load persisted state first
    sanitizeLegacyState();
    loadTheme();
    loadCurrentPage();

    // Safety: ensure a page is active. If nothing active after nav init, force tools-page.
    setTimeout(() => {
        const activePage = document.querySelector('.page.active');
        if (!activePage) {
            console.warn('[APP] No active page after initial load; forcing tools-page');
            if (typeof window.showPage === 'function') {
                window.showPage('tools-page');
            } else {
                const tools = document.getElementById('tools-page');
                if (tools) tools.classList.add('active');
            }
        }
    }, 0);

    // Then load dynamic content
    await updateServerStates();
    // Fetch and render basic stats (uptime, version)
    try {
        const { response: res, data } = await fetchJson('/_meta/stats');
        if (res.ok) {
            if (data && data.ok) {
                const uptimeEl = document.getElementById('uptime-text');
                const versionEl = document.getElementById('version-text');
                if (uptimeEl) {
                    const seconds = Math.max(0, Math.floor(data.uptimeSeconds || 0));
                    const h = Math.floor(seconds / 3600);
                    const m = Math.floor((seconds % 3600) / 60);
                    uptimeEl.textContent = `${h}h ${m}m`;
                }
                if (versionEl) {
                    versionEl.textContent = data.version || '-';
                }
            }
        }
    } catch (e) {
        console.warn('[APP] Failed to load stats:', e);
    }
    try {
        await updateLogs();
    } catch (error) {
        console.error('[APP] Failed to initialize logs:', error);
    }
    await loadConfigContent();
    await loadRequirementsContent();
    startPolling();
    
    // Debug: Verify global functions are available
    console.log('[APP] Global functions available:');
    console.log('- toggleServer:', typeof window.toggleServer);
    console.log('- toggleTool:', typeof window.toggleTool);
    console.log('- toggleTheme:', typeof window.toggleTheme);
    
    // Debug: Check if server items exist
    setTimeout(() => {
        const serverItems = document.querySelectorAll('[data-server]');
        const toggles = document.querySelectorAll('.toggle');
        const toolTags = document.querySelectorAll('.tool-tag');
        console.log(`[APP] Found ${serverItems.length} server items, ${toggles.length} toggles, ${toolTags.length} tool tags`);
        
        // Test click on first toggle if it exists
        if (toggles.length > 0) {
            const firstToggle = toggles[0];
            console.log('[APP] First toggle element:', firstToggle);
            console.log('[APP] First toggle onclick:', firstToggle.onclick);
            console.log('[APP] First toggle getAttribute onclick:', firstToggle.getAttribute('onclick'));
        }
    }, 1000);
});

// Global soft banner for unexpected errors so the UI never appears blank
function showSoftBanner(message) {
    try {
        const main = document.querySelector('.main .main-content') || document.body;
        if (!main) return;
        let banner = document.getElementById('mcpo-soft-banner');
        if (!banner) {
            banner = document.createElement('div');
            banner.id = 'mcpo-soft-banner';
            banner.style.background = 'var(--bg-secondary)';
            banner.style.color = 'var(--text-secondary)';
            banner.style.border = '1px solid var(--border)';
            banner.style.padding = '8px 12px';
            banner.style.margin = '8px 0';
            banner.style.borderRadius = '4px';
            banner.style.fontSize = '12px';
            banner.style.fontFamily = 'system-ui, sans-serif';
            main.prepend(banner);
        }
        banner.textContent = message || 'The UI hit an unexpected error. Some features may be unavailable.';
    } catch (e) {
        // ignore banner failures
    }
}

window.addEventListener('error', (ev) => {
    const msg = ev && ev.message ? ev.message : 'Unexpected error';
    console.warn('[APP] Global error:', msg, ev);
    showSoftBanner(`UI recovered from an error: ${msg}`);
});

window.addEventListener('unhandledrejection', (ev) => {
    const reason = ev && ev.reason ? (ev.reason.message || String(ev.reason)) : 'Unhandled rejection';
    console.warn('[APP] Unhandled rejection:', reason, ev);
    showSoftBanner(`A background operation failed: ${reason}`);
});
