/* MCPO API Management - Core API Functions */

// Centralized fetch wrapper to inject Authorization and always return a safe shape
async function fetchJson(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const apiKey = localStorage.getItem('mcpo-api-key');
    if (apiKey && !headers.has('Authorization')) {
        headers.set('Authorization', `Bearer ${apiKey}`);
    }
    try {
        const resp = await fetch(path, { ...options, headers });
        // Try to parse JSON consistently
        const contentType = resp.headers.get('content-type') || '';
        let data;
        if (contentType.includes('application/json')) {
            data = await resp.json();
        } else {
            const text = await resp.text();
            try { data = JSON.parse(text); } catch { data = { ok: resp.ok, text }; }
        }
        return { response: resp, data };
    } catch (err) {
        // Network error or fetch aborted; return a response-like object to avoid throwing in callers
        const fakeResp = {
            ok: false,
            status: 0,
            statusText: 'Network Error',
            headers: new Headers(),
            url: path,
        };
        const data = { ok: false, error: String(err), detail: 'Network error contacting API' };
        console.warn('[API] fetchJson network error:', err);
        return { response: fakeResp, data };
    }
}

function getApiErrorMessage(response, data, fallbackMessage) {
    const apiError = data && data.error;
    if (apiError && typeof apiError === 'object' && apiError.message) {
        return String(apiError.message);
    }
    if (typeof apiError === 'string' && apiError) {
        return apiError;
    }
    if (data && typeof data.detail === 'string' && data.detail) {
        return data.detail;
    }
    if (data && data.detail && typeof data.detail.message === 'string') {
        return data.detail.message;
    }
    if (response && response.status) {
        return fallbackMessage + ' (HTTP ' + response.status + ')';
    }
    return fallbackMessage;
}

async function fetchMetaData(path, options = {}, fallbackMessage = 'API request failed') {
    const { response, data } = await fetchJson(path, options);
    if (!response.ok || !data || data.ok !== true) {
        const error = new Error(getApiErrorMessage(response, data, fallbackMessage));
        error.status = response.status || 0;
        error.code = data && data.error && data.error.code
            ? String(data.error.code)
            : '';
        error.details = data && data.error ? data.error.details : undefined;
        throw error;
    }
    return data;
}

function requireApiValue(data, key, validator, label) {
    const value = data ? data[key] : undefined;
    if (!validator(value)) {
        throw new Error('API returned an invalid ' + label + ' response.');
    }
    return value;
}

// Real API Functions
async function fetchServers() {
    try {
        const { response, data } = await fetchJson('/_meta/servers');
        if (data.ok) {
            return data.servers;
        }
        console.error('Failed to fetch servers:', data);
        return [];
    } catch (error) {
        console.error('Error fetching servers:', error);
        return [];
    }
}

async function fetchServerTools(serverName) {
    try {
        const { response, data } = await fetchJson(`/_meta/servers/${serverName}/tools`);
        if (data.ok) {
            return data.tools;
        }
        console.error(`Failed to fetch tools for ${serverName}:`, data);
        return [];
    } catch (error) {
        console.error(`Error fetching tools for ${serverName}:`, error);
        return [];
    }
}

async function toggleServerEnabled(serverName, enabled) {
    try {
        const endpoint = enabled ? 'enable' : 'disable';
        const url = `/_meta/servers/${serverName}/${endpoint}`;
        console.log(`[API] Calling ${url}`);
        
        console.log(`[API] Starting fetch to ${url}...`);
        const { response, data } = await fetchJson(url, { method: 'POST' });
        
        console.log(`[API] Fetch completed. Response status: ${response.status}`);
        console.log(`[API] Response ok: ${response.ok}`);
        
        if (!response.ok) {
            console.error(`[API] HTTP error! status: ${response.status}`);
            return false;
        }
        
        console.log(`[API] Parsing JSON response...`);
        
        console.log(`[API] Response data:`, data);
        
        if (!data || data.ok !== true) {
            console.error(`[API] API returned ok=false for ${endpoint} server ${serverName}:`, data);
            return false;
        }
        
        console.log(`[API] Successfully ${endpoint}d server ${serverName}`);
        return true;
    } catch (error) {
        console.error(`[API] Error toggling server ${serverName}:`, error);
        return false;
    }
}

async function toggleToolEnabled(serverName, toolName, enabled) {
    try {
        const endpoint = enabled ? 'enable' : 'disable';
        const url = `/_meta/servers/${serverName}/tools/${toolName}/${endpoint}`;
        console.log(`[API] Calling ${url}`);
        
        console.log(`[API] Starting fetch to ${url}...`);
        const { response, data } = await fetchJson(url, { method: 'POST' });
        
        console.log(`[API] Fetch completed. Response status: ${response.status}`);
        console.log(`[API] Response ok: ${response.ok}`);
        
        if (!response.ok) {
            console.error(`[API] HTTP error! status: ${response.status}`);
            return false;
        }
        
        console.log(`[API] Parsing JSON response...`);
        
        console.log(`[API] Response data:`, data);
        
        if (!data || data.ok !== true) {
            console.error(`[API] API returned ok=false for ${endpoint} tool ${toolName}:`, data);
            return false;
        }
        
        console.log(`[API] Successfully ${endpoint}d tool ${toolName}`);
        return true;
    } catch (error) {
        console.error(`[API] Error toggling tool ${toolName}:`, error);
        return false;
    }
}

async function reloadConfig() {
    try {
        const { response, data } = await fetchJson('/_meta/reload', { method: 'POST' });
        if (data.ok) {
            console.log('Config reloaded successfully');
            await updateServerStates();
        } else {
            console.error('Failed to reload config:', data);
        }
        return data.ok;
    } catch (error) {
        console.error('Error reloading config:', error);
        return false;
    }
}

async function reinitServer(serverName) {
    try {
        const { response, data } = await fetchJson(`/_meta/reinit/${serverName}`, { method: 'POST' });
        if (data.ok) {
            console.log(`Server ${serverName} reinitialized successfully`);
            await updateServerStates();
        } else {
            console.error(`Failed to reinitialize server ${serverName}:`, data);
        }
        return data.ok;
    } catch (error) {
        console.error(`Error reinitializing server ${serverName}:`, error);
        return false;
    }
}

// Config management API
async function loadConfigContent() {
    try {
        const { response, data } = await fetchJson('/_meta/config/content');
        if (data.ok) {
            const editor = document.getElementById('server-config-editor');
            if (editor) {
                editor.value = data.content;
            }
        } else {
            console.error('Failed to load config:', data);
        }
    } catch (error) {
        console.error('Error loading config:', error);
    }
}

async function saveConfigContent() {
    const editor = document.getElementById('server-config-editor');
    if (!editor) return;
    
    try {
        const { response, data } = await fetchJson('/_meta/config/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content: editor.value })
        });
        if (data.ok) {
            console.log('Config saved successfully');
            await updateServerStates();
        } else {
            console.error('Failed to save config:', data);
        }
    } catch (error) {
        console.error('Error saving config:', error);
    }
}

async function saveRequirements() {
    const editor = document.getElementById('requirements-editor');
    if (!editor) return;
    
    try {
        const { response, data } = await fetchJson('/_meta/requirements/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content: editor.value })
        });
        if (data.ok) {
            console.log('Requirements saved successfully');
        } else {
            console.error('Failed to save requirements:', data);
        }
    } catch (error) {
        console.error('Error saving requirements:', error);
    }
}

async function installDependencies() {
    try {
        const { response, data } = await fetchJson('/_meta/install-dependencies', { method: 'POST' });
        if (data.ok) {
            console.log('Dependencies installation started');
        } else {
            console.error('Failed to install dependencies:', data);
            alert('Failed to install dependencies. Check logs for details.');
        }
    } catch (error) {
        console.error('Error installing dependencies:', error);
        alert('Error installing dependencies. Check logs for details.');
    }
}

async function loadRequirementsContent() {
    try {
        const { response, data } = await fetchJson('/_meta/requirements/content');
        if (data.ok) {
            const editor = document.getElementById('requirements-editor');
            if (editor) {
                editor.value = data.content;
            }
        } else {
            console.error('Failed to load requirements:', data);
        }
    } catch (error) {
        console.error('Error loading requirements:', error);
    }
}

async function loadAboutContent() {
    const aboutPage = document.getElementById('about-page');
    if (!aboutPage) return;
    
    // Only load once
    if (aboutPage.innerHTML.trim()) return;
    
    try {
        // Load version info
        const versionResp = await fetch('/_meta/metrics');
        let version = '1.0.0-rc1';
        if (versionResp.ok) {
            const metrics = await versionResp.json();
            version = metrics.version || version;
        }
        
        // Load about page content
        const resp = await fetch('/ui/about.html');
        if (resp.ok) {
            const content = await resp.text();
            aboutPage.innerHTML = content.trim() ? content : '<p>About page not found.</p>';
            
            // Update version in the about page
            const versionSpan = document.getElementById('about-version');
            if (versionSpan) {
                versionSpan.textContent = version;
            }
        } else {
            aboutPage.innerHTML = '<p>Failed to load about page.</p>';
        }
    } catch (error) {
        console.error('Error loading about page:', error);
        aboutPage.innerHTML = '<p>Error loading about page.</p>';
    }
}

// Minimal markdown renderer for the changelog page (headings, lists, bold,
// code blocks, inline code, links). Escapes all HTML first.
function renderChangelogMarkdown(md) {
    const escape = (s) => s
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    const inline = (s) => s
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

    const lines = escape(md).split(/\r?\n/);
    const out = [];
    let inList = false;
    let inCode = false;
    const closeList = () => { if (inList) { out.push('</ul>'); inList = false; } };

    for (const line of lines) {
        if (line.startsWith('```')) {
            closeList();
            out.push(inCode ? '</code></pre>' : '<pre><code>');
            inCode = !inCode;
            continue;
        }
        if (inCode) { out.push(line); continue; }
        const h = line.match(/^(#{1,4})\s+(.*)$/);
        if (h) {
            closeList();
            const level = Math.min(h[1].length + 1, 5); // demote: page h1 exists
            out.push(`<h${level}>${inline(h[2])}</h${level}>`);
            continue;
        }
        const li = line.match(/^\s*[-*]\s+(.*)$/);
        if (li) {
            if (!inList) { out.push('<ul>'); inList = true; }
            out.push(`<li>${inline(li[1])}</li>`);
            continue;
        }
        if (!line.trim()) { closeList(); continue; }
        closeList();
        out.push(`<p>${inline(line)}</p>`);
    }
    closeList();
    if (inCode) out.push('</code></pre>');
    return out.join('\n');
}

async function loadChangelogContent() {
    const page = document.getElementById('changelog-page');
    if (!page) return;
    // Only load once per session
    if (page.innerHTML.trim()) return;
    try {
        const resp = await fetch('/_meta/changelog');
        const data = await resp.json();
        if (resp.ok && data.ok && typeof data.content === 'string') {
            page.innerHTML = `<div class="changelog-content">${renderChangelogMarkdown(data.content)}</div>`;
        } else {
            const msg = (data && data.error && data.error.message) || 'CHANGELOG.md not found.';
            page.innerHTML = `<div class="empty-state">${msg}</div>`;
        }
    } catch (error) {
        console.error('Error loading changelog:', error);
        page.innerHTML = '<div class="empty-state">Failed to load changelog. Check that the server is running.</div>';
    }
}

async function fetchSkills() {
    const catalog = await fetchSkillsCatalog();
    return catalog.skills;
}

async function fetchSkillsCatalog() {
    const data = await fetchMetaData('/_meta/skills', {}, 'Failed to load skills');
    return {
        skills: requireApiValue(data, 'skills', Array.isArray, 'skills'),
        issues: Array.isArray(data.issues) ? data.issues : [],
    };
}

async function fetchSkill(skillId) {
    const data = await fetchMetaData(
        '/_meta/skills/' + encodeURIComponent(skillId),
        {},
        'Failed to load skill ' + skillId,
    );
    return requireApiValue(
        data,
        'skill',
        (value) => !!value && typeof value === 'object',
        'skill',
    );
}

async function saveSkill(payload) {
    await fetchMetaData('/_meta/skills', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    }, 'Failed to save skill');
    return true;
}

async function deleteSkill(skillId) {
    await fetchMetaData('/_meta/skills/' + encodeURIComponent(skillId), {
        method: 'DELETE',
    }, 'Failed to delete skill ' + skillId);
    return true;
}

async function setSkillEnabled(skillId, enabled) {
    const suffix = enabled ? 'enable' : 'disable';
    const path = '/_meta/skills/' + encodeURIComponent(skillId) + '/' + suffix;
    await fetchMetaData(path, {
        method: 'POST',
    }, 'Failed to ' + suffix + ' skill ' + skillId);
    return true;
}

async function fetchCliPackages() {
    const data = await fetchMetaData(
        '/_meta/cli-packages',
        {},
        'Failed to load installed CLI packages',
    );
    return requireApiValue(data, 'packages', Array.isArray, 'CLI packages');
}

async function planCliPackage(payload) {
    const data = await fetchMetaData('/_meta/cli-packages/plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    }, 'Failed to plan CLI package installation');
    return requireApiValue(
        data,
        'plan',
        (value) => !!value && typeof value === 'object',
        'CLI package plan',
    );
}

async function installCliPackage(payload) {
    const data = await fetchMetaData('/_meta/cli-packages/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    }, 'Failed to install CLI package');
    return requireApiValue(
        data,
        'package',
        (value) => !!value && typeof value === 'object',
        'installed CLI package',
    );
}

async function uninstallCliPackage(packageId, confirmed) {
    const path = '/_meta/cli-packages/' + encodeURIComponent(packageId) + '/uninstall';
    return fetchMetaData(
        path,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirmed: confirmed === true }),
        },
        'Failed to uninstall CLI package ' + packageId,
    );
}

async function fetchSkillPackages() {
    const data = await fetchMetaData(
        '/_meta/skill-packages',
        {},
        'Failed to load installed skill packages',
    );
    return requireApiValue(data, 'packages', Array.isArray, 'skill packages');
}

async function inspectSkillPackage(payload) {
    const data = await fetchMetaData('/_meta/skill-packages/inspect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    }, 'Failed to inspect skill package');
    return requireApiValue(
        data,
        'inspection',
        (value) => !!value && typeof value === 'object',
        'skill package inspection',
    );
}

async function previewToolManifestArchive(payload) {
    const data = await fetchMetaData('/_meta/tool-manifests/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    }, 'Failed to preview TOOL.yaml proposal');
    return requireApiValue(
        data,
        'preview',
        (value) => !!value && typeof value === 'object',
        'TOOL.yaml proposal preview',
    );
}

async function installSkillPackage(payload) {
    const data = await fetchMetaData('/_meta/skill-packages/install', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    }, 'Failed to install skill package');
    return requireApiValue(
        data,
        'package',
        (value) => !!value && typeof value === 'object',
        'installed skill package',
    );
}

async function uninstallSkillPackage(packageId, confirmed) {
    const path = '/_meta/skill-packages/' + encodeURIComponent(packageId) + '/uninstall';
    return fetchMetaData(
        path,
        {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirmed: confirmed === true }),
        },
        'Failed to uninstall skill package ' + packageId,
    );
}

// Expose for inline handlers
window.saveConfigContent = saveConfigContent;
window.installDependencies = installDependencies;
window.saveRequirements = saveRequirements;
window.loadAboutContent = loadAboutContent;
window.loadChangelogContent = loadChangelogContent;
window.fetchSkills = fetchSkills;
window.fetchSkillsCatalog = fetchSkillsCatalog;
window.fetchSkill = fetchSkill;
window.saveSkill = saveSkill;
window.deleteSkill = deleteSkill;
window.setSkillEnabled = setSkillEnabled;
window.fetchCliPackages = fetchCliPackages;
window.planCliPackage = planCliPackage;
window.installCliPackage = installCliPackage;
window.uninstallCliPackage = uninstallCliPackage;
window.fetchSkillPackages = fetchSkillPackages;
window.inspectSkillPackage = inspectSkillPackage;
window.previewToolManifestArchive = previewToolManifestArchive;
window.installSkillPackage = installSkillPackage;
window.uninstallSkillPackage = uninstallSkillPackage;
