/* MCPO Server Management - Server State & Tool Management */

// Server State Management - Connected to real backend
let serverStates = {};
const expandedServers = new Set();

function cloneServerStates(states) {
    try {
        if (typeof structuredClone === 'function') {
            return structuredClone(states);
        }
    } catch (error) {
        console.warn('[SERVERS] structuredClone failed, falling back to JSON clone:', error);
    }
    return JSON.parse(JSON.stringify(states || {}));
}

function getServerStatesSnapshot() {
    return cloneServerStates(serverStates);
}

// Server Expansion Management
function toggleServerExpansion(serverName) {
    const serverItem = document.querySelector(`[data-server="${serverName}"]`);
    const dropdown = serverItem.querySelector('.tools-dropdown');
    
    serverItem.classList.toggle('expanded');
    dropdown.classList.toggle('expanded');

    if (serverItem.classList.contains('expanded')) {
        expandedServers.add(serverName);
    } else {
        expandedServers.delete(serverName);
    }
}

// Server Toggle Management - Connected to backend
async function toggleServer(toggle, serverName) {
    console.log(`[DEBUG] toggleServer called: ${serverName}, current state:`, serverStates[serverName]);
    
    const currentState = serverStates[serverName];
    if (!currentState) {
        console.error(`[ERROR] No state found for server: ${serverName}`);
        return;
    }
    
    const newEnabled = !currentState.enabled;
    console.log(`[DEBUG] Toggling ${serverName} from ${currentState.enabled} to ${newEnabled}`);
    
    // Optimistically update UI
    toggle.classList.toggle('on', newEnabled);
    
    // Call backend API
    const success = await toggleServerEnabled(serverName, newEnabled);
    console.log(`[DEBUG] API call result for ${serverName}:`, success);
    
    if (success) {
        // Update local state
        serverStates[serverName].enabled = newEnabled;
        updateServerVisuals(serverName);
        updateServerHeaderCounts(serverName);
        updateStatusCounts();
        console.log(`[DEBUG] Successfully toggled ${serverName}, new state:`, serverStates[serverName]);
    } else {
        // Revert UI on failure
        toggle.classList.toggle('on', !newEnabled);
        console.error(`[ERROR] Failed to toggle server ${serverName}`);
    }
}

// Tool Toggle Management - Connected to backend
async function toggleTool(toolElement, toolName) {
    if (toolElement.classList.contains('inactive')) {
        console.log(`[DEBUG] Tool ${toolName} is inactive, ignoring click`);
        return;
    }
    
    const serverName = toolElement.closest('[data-server]').getAttribute('data-server');
    console.log(`[DEBUG] toggleTool called: ${toolName} on ${serverName}`);
    
    if (!serverStates[serverName] || !serverStates[serverName].tools.hasOwnProperty(toolName)) {
        console.error(`[ERROR] Tool ${toolName} not found in server ${serverName} state`);
        return;
    }
    
    const currentState = serverStates[serverName].tools[toolName];
    const newEnabled = !currentState;
    console.log(`[DEBUG] Toggling tool ${toolName} from ${currentState} to ${newEnabled}`);
    
    // Optimistically update UI
    if (newEnabled) {
        toolElement.classList.remove('disabled');
        toolElement.classList.add('enabled');
    } else {
        toolElement.classList.remove('enabled');
        toolElement.classList.add('disabled');
    }
    
    // Call backend API
    const success = await toggleToolEnabled(serverName, toolName, newEnabled);
    console.log(`[DEBUG] Tool API call result for ${toolName}:`, success);
    
    if (success) {
        // Update local state
        serverStates[serverName].tools[toolName] = newEnabled;
        updateServerHeaderCounts(serverName);
        updateStatusCounts();
        console.log(`[DEBUG] Successfully toggled tool ${toolName}, new state:`, serverStates[serverName].tools[toolName]);
    } else {
        // Revert UI on failure
        if (!newEnabled) {
            toolElement.classList.remove('disabled');
            toolElement.classList.add('enabled');
        } else {
            toolElement.classList.remove('enabled');
            toolElement.classList.add('disabled');
        }
        console.error(`[ERROR] Failed to toggle tool ${toolName} on server ${serverName}`);
    }
}

// Update Status Counts
function updateStatusCounts() {
    let activeServers = 0;
    let enabledTools = 0;
    
    Object.entries(serverStates).forEach(([serverName, state]) => {
        if (state.enabled && state.connected) {
            activeServers++;
        }
        
        if (state.enabled) {
            Object.values(state.tools).forEach(toolEnabled => {
                if (toolEnabled) enabledTools++;
            });
        }
    });
    
    document.getElementById('server-count').textContent = `${activeServers} active`;
    document.getElementById('tool-count').textContent = `${enabledTools} enabled`;
    
    // Show/hide tools warning based on 40 tool limit
    const warningElement = document.getElementById('tools-warning');
    const totalToolsElement = document.getElementById('total-tools-count');
    
    if (enabledTools > 40) {
        totalToolsElement.textContent = enabledTools;
        warningElement.style.display = 'flex';
    } else {
        warningElement.style.display = 'none';
    }
}

// Real server state management
async function updateServerStates() {
    try {
        const servers = await fetchServers();
        const newStates = {};
        
        const toolResults = await Promise.allSettled(
            servers.map(server => fetchServerTools(server.name))
        );

        servers.forEach((server, idx) => {
            const result = toolResults[idx];
            const tools = result && result.status === 'fulfilled' && Array.isArray(result.value)
                ? result.value
                : [];
            if (result && result.status === 'rejected') {
                console.warn(`[WARN] Failed to fetch tools for ${server.name}:`, result.reason);
            }
            const toolStates = {};
            if (tools && tools.length > 0) {
                tools.forEach(tool => {
                    toolStates[tool.name] = tool.enabled;
                });
            }
            
            newStates[server.name] = {
                enabled: server.enabled,
                connected: server.connected || server.status === 'connected',
                tools: toolStates,
                basePath: server.basePath || `/${server.name}/`
            };
        });
        
        serverStates = newStates;
        // Prune expansion state for servers that no longer exist
        for (const name of Array.from(expandedServers)) {
            if (!serverStates[name]) {
                expandedServers.delete(name);
            }
        }
        await renderServerList();
        updateStatusCounts();
        if (typeof document !== 'undefined') {
            const snapshot = cloneServerStates(serverStates);
            document.dispatchEvent(new CustomEvent('mcpo:server-states-updated', {
                detail: { serverStates: snapshot }
            }));
        }
    } catch (error) {
        console.error('Failed to update server states:', error);
    }
}

async function renderServerList() {
    // Scope rendering to the Tools page card to avoid selecting the wrong container
    const container = document.querySelector('#tools-page .card .card-content');
    if (!container) return;
    
    container.innerHTML = '';

    const entries = Object.entries(serverStates || {});
    if (entries.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'empty-state';
        empty.style.padding = '16px';
        empty.style.color = 'var(--text-secondary)';
        empty.innerHTML = '<strong>No servers to display.</strong><br><span>API may be unavailable or no MCP servers are configured. The UI will continue to operate in a read-only state.</span>';
        container.appendChild(empty);
        return;
    }

    for (const [serverName, state] of entries) {
        const serverItem = createServerItem(serverName, state);
        container.appendChild(serverItem);
    }
}

function createServerItem(serverName, state) {
    const serverItem = document.createElement('div');
    serverItem.className = 'server-item';
    serverItem.setAttribute('data-server', serverName);
    
    const toolCount = Object.keys(state.tools).length;
    const enabledToolCount = state.enabled ? Object.values(state.tools).filter(Boolean).length : 0;
    const statusText = state.connected ? 'Connected • Ready' : 'Disconnected • Error';
    const toolText = state.connected ? `${enabledToolCount}/${toolCount} tools enabled` : `${toolCount} tools unavailable`;
    
    // Create safe HTML using DOM methods to prevent XSS
    const serverHeader = document.createElement('div');
    serverHeader.className = 'server-header';
    serverHeader.onclick = () => toggleServerExpansion(serverName);
    
    const serverInfo = document.createElement('div');
    serverInfo.className = 'server-info';
    
    const nameRow = document.createElement('div');
    nameRow.className = 'server-name-row';
    
    const nameEl = document.createElement('div');
    nameEl.className = 'server-name';
    nameEl.textContent = serverName;
    
    const beacon = document.createElement('div');
    beacon.className = `status-beacon ${state.enabled && state.connected ? 'connected' : state.enabled ? 'error' : 'hidden'}`;
    
    nameRow.appendChild(nameEl);
    nameRow.appendChild(beacon);
    
    const statusEl = document.createElement('div');
    statusEl.className = 'server-status';
    statusEl.textContent = statusText;
    
    const toolCountEl = document.createElement('div');
    toolCountEl.className = 'tool-count';
    toolCountEl.textContent = toolText;
    
    serverInfo.appendChild(nameRow);
    serverInfo.appendChild(statusEl);
    serverInfo.appendChild(toolCountEl);
    
    const controls = document.createElement('div');
    controls.className = 'server-controls';
    
    const docsLink = document.createElement('a');
    docsLink.href = `/${serverName}/docs`;
    docsLink.target = '_blank';
    docsLink.className = 'btn';
    docsLink.textContent = 'Docs';
    docsLink.onclick = (e) => e.stopPropagation();
    
    const toggle = document.createElement('div');
    toggle.className = `toggle ${state.enabled ? 'on' : ''}`;
    // Bind via JS and also via attribute to ensure global handler works across environments
    toggle.onclick = (e) => {
        e.stopPropagation();
        toggleServer(toggle, serverName);
    };
    toggle.setAttribute('onclick', `event.stopPropagation(); toggleServer(this, '${serverName}')`);
    
    const expandIcon = document.createElement('svg');
    expandIcon.className = 'expand-icon';
    expandIcon.setAttribute('viewBox', '0 0 24 24');
    expandIcon.setAttribute('fill', 'none');
    expandIcon.setAttribute('stroke', 'currentColor');
    expandIcon.setAttribute('stroke-width', '2');
    expandIcon.innerHTML = '<polyline points="9,18 15,12 9,6"></polyline>';
    
    controls.appendChild(docsLink);
    controls.appendChild(toggle);
    controls.appendChild(expandIcon);
    
    serverHeader.appendChild(serverInfo);
    serverHeader.appendChild(controls);
    
    const dropdown = document.createElement('div');
    dropdown.className = 'tools-dropdown';
    
    const toolsGrid = document.createElement('div');
    toolsGrid.className = 'tools-grid';
    
    // Create tool tags safely
    Object.entries(state.tools).forEach(([toolName, enabled]) => {
        const toolTag = document.createElement('span');
        toolTag.className = `tool-tag ${enabled ? 'enabled' : 'disabled'} ${state.enabled && state.connected ? '' : 'inactive'}`;
        toolTag.textContent = toolName;
        toolTag.onclick = () => toggleTool(toolTag, toolName);
        toolTag.setAttribute('onclick', `toggleTool(this, '${toolName}')`);
        toolsGrid.appendChild(toolTag);
    });
    
    dropdown.appendChild(toolsGrid);
    
    serverItem.appendChild(serverHeader);
    serverItem.appendChild(dropdown);

    if (expandedServers.has(serverName)) {
        serverItem.classList.add('expanded');
        dropdown.classList.add('expanded');
    }
    
    return serverItem;
}

function updateServerVisuals(serverName) {
    const serverItem = document.querySelector(`[data-server="${serverName}"]`);
    if (!serverItem) return;
    
    const state = serverStates[serverName];
    const beacon = serverItem.querySelector('.status-beacon');
    const tools = serverItem.querySelectorAll('.tool-tag');
    
    if (state.enabled && state.connected) {
        beacon.className = 'status-beacon connected';
        tools.forEach(tool => tool.classList.remove('inactive'));
    } else if (state.enabled && !state.connected) {
        beacon.className = 'status-beacon error';
        tools.forEach(tool => tool.classList.add('inactive'));
    } else {
        // When server is disabled, show red per UX spec
        beacon.className = 'status-beacon error';
        tools.forEach(tool => tool.classList.add('inactive'));
    }
}

function updateServerHeaderCounts(serverName) {
    const serverItem = document.querySelector(`[data-server="${serverName}"]`);
    if (!serverItem) return;
    const state = serverStates[serverName];
    const toolCount = Object.keys(state.tools).length;
    const enabledToolCount = state.enabled ? Object.values(state.tools).filter(Boolean).length : 0;
    const toolText = state.connected ? `${enabledToolCount}/${toolCount} tools enabled` : `${toolCount} tools unavailable`;
    const toolCountEl = serverItem.querySelector('.tool-count');
    if (toolCountEl) toolCountEl.textContent = toolText;
}

async function restartAllServers() {
    try {
        const success = await reloadConfig();
        if (success) {
            console.log('All servers restarted');
        } else {
            console.error('Failed to restart servers');
        }
    } catch (error) {
        console.error('Error restarting servers:', error);
    }
}

// Expose for inline handlers used in dynamic HTML (if any existing)
window.toggleServer = toggleServer;
window.toggleTool = toggleTool;
window.toggleServerExpansion = toggleServerExpansion;
window.restartAllServers = restartAllServers;
window.getServerStatesSnapshot = getServerStatesSnapshot;

// Robust delegated handlers as a safety net for dynamically created elements
document.addEventListener('click', function(e) {
    const toggleEl = e.target.closest('.toggle');
    if (toggleEl && toggleEl.closest('[data-server]')) {
        e.stopPropagation();
        const serverName = toggleEl.closest('[data-server]').getAttribute('data-server');
        toggleServer(toggleEl, serverName);
        return;
    }
    const toolEl = e.target.closest('.tool-tag');
    if (toolEl && toolEl.closest('[data-server]')) {
        const serverName = toolEl.closest('[data-server]').getAttribute('data-server');
        const toolName = toolEl.textContent.trim();
        toggleTool(toolEl, toolName);
    }
});
