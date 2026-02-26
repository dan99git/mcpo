/* MCPO Skills Page — Split-panel layout with folder grouping */

const skillsState = {
    initialized: false,
    list: [],
    selectedId: null,
    isNew: false,
    collapsedFolders: new Set(),
};

function getSkillsElements() {
    return {
        listContainer: document.getElementById('skills-list'),
        emptyState: document.getElementById('skills-empty-state'),
        editor: document.getElementById('skills-editor'),
        editorMode: document.getElementById('skills-editor-mode'),
        newBtn: document.getElementById('skills-new-btn'),
        deleteBtn: document.getElementById('skills-delete-btn'),
        saveBtn: document.getElementById('skills-save-btn'),
        idInput: document.getElementById('skills-id-input'),
        titleInput: document.getElementById('skills-title-input'),
        descInput: document.getElementById('skills-description-input'),
        priorityInput: document.getElementById('skills-priority-input'),
        scopesInput: document.getElementById('skills-scopes-input'),
        providersInput: document.getElementById('skills-providers-input'),
        modelsInput: document.getElementById('skills-models-input'),
        sourcePath: document.getElementById('skills-source-path'),
        contentInput: document.getElementById('skills-content-input'),
    };
}

// --- Helpers ---

function csvToArray(str) {
    return (str || '').split(',').map((s) => s.trim()).filter(Boolean);
}

function arrayToCsv(arr) {
    return (arr || []).join(', ');
}

/** Group skills by folder. Returns Map<string, skill[]> preserving insertion order. */
function groupByFolder(skills) {
    const groups = new Map();
    for (const skill of skills) {
        const folder = skill.folder || '';
        if (!groups.has(folder)) groups.set(folder, []);
        groups.get(folder).push(skill);
    }
    // Sort: root ("") first, then folders alphabetically
    const sorted = new Map();
    if (groups.has('')) {
        sorted.set('', groups.get(''));
        groups.delete('');
    }
    const folderKeys = [...groups.keys()].sort();
    for (const key of folderKeys) {
        sorted.set(key, groups.get(key));
    }
    return sorted;
}

// --- List rendering ---

function renderSkillsList() {
    const els = getSkillsElements();
    if (!els.listContainer) return;
    els.listContainer.innerHTML = '';

    if (!skillsState.list.length) {
        const empty = document.createElement('div');
        empty.className = 'sk-list-empty';
        empty.textContent = 'No skills yet';
        els.listContainer.appendChild(empty);
        return;
    }

    const groups = groupByFolder(skillsState.list);
    const multipleGroups = groups.size > 1 || (groups.size === 1 && !groups.has(''));

    for (const [folder, skills] of groups) {
        // Only show folder headers when there's a real folder structure
        if (multipleGroups) {
            const folderEl = renderFolderHeader(folder, skills);
            els.listContainer.appendChild(folderEl);
        }

        const isCollapsed = skillsState.collapsedFolders.has(folder);

        if (!isCollapsed) {
            for (const skill of skills) {
                const card = renderSkillCard(skill, multipleGroups);
                els.listContainer.appendChild(card);
            }
        }
    }
}

function renderFolderHeader(folder, skills) {
    const header = document.createElement('div');
    header.className = 'sk-folder-header';

    const isCollapsed = skillsState.collapsedFolders.has(folder);
    if (isCollapsed) header.classList.add('collapsed');

    const chevron = document.createElement('span');
    chevron.className = 'sk-folder-chevron';
    chevron.textContent = isCollapsed ? '\u25B6' : '\u25BC';

    const icon = document.createElement('span');
    icon.className = 'sk-folder-icon';
    icon.textContent = isCollapsed ? '\uD83D\uDCC1' : '\uD83D\uDCC2';

    const name = document.createElement('span');
    name.className = 'sk-folder-name';
    name.textContent = folder || 'Root';

    const count = document.createElement('span');
    count.className = 'sk-folder-count';
    count.textContent = skills.length;

    header.appendChild(chevron);
    header.appendChild(icon);
    header.appendChild(name);
    header.appendChild(count);

    header.addEventListener('click', () => {
        if (skillsState.collapsedFolders.has(folder)) {
            skillsState.collapsedFolders.delete(folder);
        } else {
            skillsState.collapsedFolders.add(folder);
        }
        renderSkillsList();
    });

    return header;
}

function renderSkillCard(skill, nested) {
    const card = document.createElement('div');
    card.className = 'sk-card';
    if (nested) card.classList.add('sk-card-nested');
    if (skill.id === skillsState.selectedId) card.classList.add('selected');

    const info = document.createElement('div');
    info.className = 'sk-card-info';
    info.addEventListener('click', () => selectSkill(skill.id));

    const title = document.createElement('div');
    title.className = 'sk-card-title';
    title.textContent = skill.title || skill.id;

    const desc = document.createElement('div');
    desc.className = 'sk-card-desc';
    desc.textContent = skill.description || skill.id;

    info.appendChild(title);
    info.appendChild(desc);

    // Show folder badge on card when no folder grouping visible (flat list)
    if (!nested && skill.folder) {
        const badge = document.createElement('span');
        badge.className = 'sk-card-folder-badge';
        badge.textContent = skill.folder;
        info.appendChild(badge);
    }

    const toggle = document.createElement('div');
    toggle.className = 'toggle' + (skill.enabled ? ' on' : '');
    toggle.title = skill.enabled ? 'Enabled \u2014 click to disable' : 'Disabled \u2014 click to enable';
    toggle.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleSkillEnabled(skill.id, !skill.enabled, toggle);
    });

    card.appendChild(info);
    card.appendChild(toggle);
    return card;
}

// --- Skill selection ---

async function selectSkill(skillId) {
    const els = getSkillsElements();
    skillsState.selectedId = skillId;
    skillsState.isNew = false;
    renderSkillsList();
    showEditor(true);

    const skill = await fetchSkill(skillId);
    if (!skill) return;
    els.idInput.value = skill.id || '';
    els.idInput.readOnly = true;
    els.titleInput.value = skill.title || '';
    els.descInput.value = skill.description || '';
    els.priorityInput.value = skill.priority ?? 100;
    els.scopesInput.value = arrayToCsv(skill.scopes);
    els.providersInput.value = arrayToCsv(skill.providers);
    els.modelsInput.value = arrayToCsv(skill.models);
    els.contentInput.value = skill.content || '';
    els.editorMode.textContent = 'Editing';
    els.deleteBtn.style.display = '';

    if (els.sourcePath) {
        const parts = [];
        if (skill.folder) parts.push(skill.folder + '/');
        if (skill.sourcePath) parts.push(skill.sourcePath);
        if (parts.length) {
            els.sourcePath.textContent = skill.sourcePath || '';
            els.sourcePath.style.display = '';
        } else {
            els.sourcePath.style.display = 'none';
        }
    }
}

function startNewSkill() {
    const els = getSkillsElements();
    skillsState.selectedId = null;
    skillsState.isNew = true;
    renderSkillsList();
    showEditor(true);

    els.idInput.value = '';
    els.idInput.readOnly = false;
    els.titleInput.value = '';
    els.descInput.value = '';
    els.priorityInput.value = 100;
    els.scopesInput.value = 'chat, completions';
    els.providersInput.value = '';
    els.modelsInput.value = '';
    els.contentInput.value = '';
    els.editorMode.textContent = 'New Skill';
    els.deleteBtn.style.display = 'none';
    if (els.sourcePath) els.sourcePath.style.display = 'none';
    els.idInput.focus();
}

function showEditor(visible) {
    const els = getSkillsElements();
    if (els.editor) els.editor.style.display = visible ? '' : 'none';
    if (els.emptyState) els.emptyState.style.display = visible ? 'none' : '';
}

// --- Toggle enable/disable ---

async function toggleSkillEnabled(skillId, enabled, toggleEl) {
    // Optimistic
    toggleEl.classList.toggle('on', enabled);
    toggleEl.title = enabled ? 'Enabled \u2014 click to disable' : 'Disabled \u2014 click to enable';

    const ok = await setSkillEnabled(skillId, enabled);
    if (!ok) {
        toggleEl.classList.toggle('on', !enabled);
        toggleEl.title = !enabled ? 'Enabled \u2014 click to disable' : 'Disabled \u2014 click to enable';
        return;
    }
    // Update local list
    const item = skillsState.list.find((s) => s.id === skillId);
    if (item) item.enabled = enabled;
}

// --- Save ---

async function saveSkillFromEditor() {
    const els = getSkillsElements();
    if (!els.idInput) return;
    const scopes = csvToArray(els.scopesInput.value);
    const providers = csvToArray(els.providersInput.value);
    const models = csvToArray(els.modelsInput.value);
    const payload = {
        id: (els.idInput.value || '').trim(),
        title: (els.titleInput.value || '').trim(),
        description: (els.descInput.value || '').trim(),
        content: els.contentInput.value || '',
        priority: parseInt(els.priorityInput.value, 10) || 100,
        scopes: scopes.length ? scopes : null,
        providers: providers.length ? providers : null,
        models: models.length ? models : null,
    };
    if (!payload.id || !payload.title || !payload.content.trim()) {
        alert('ID, title, and content are required.');
        return;
    }
    const ok = await saveSkill(payload);
    if (!ok) {
        alert('Failed to save skill.');
        return;
    }
    skillsState.selectedId = payload.id;
    skillsState.isNew = false;
    await refreshSkillsPanel();
    // Visual feedback
    if (els.saveBtn) {
        const orig = els.saveBtn.textContent;
        els.saveBtn.textContent = 'Saved';
        setTimeout(() => { els.saveBtn.textContent = orig; }, 1200);
    }
}

// --- Delete ---

async function deleteSelectedSkill() {
    if (!skillsState.selectedId) return;
    const skill = skillsState.list.find((s) => s.id === skillsState.selectedId);
    const name = skill ? skill.title : skillsState.selectedId;
    if (!confirm(`Delete skill "${name}"? This cannot be undone.`)) return;

    const ok = await deleteSkill(skillsState.selectedId);
    if (!ok) {
        alert('Failed to delete skill.');
        return;
    }
    skillsState.selectedId = null;
    skillsState.isNew = false;
    showEditor(false);
    await refreshSkillsPanel();
}

// --- Refresh ---

async function refreshSkillsPanel() {
    skillsState.list = await fetchSkills();
    renderSkillsList();
    if (skillsState.selectedId) {
        await selectSkill(skillsState.selectedId);
    }
}

// --- Bind & Init ---

function bindSkillsHandlers() {
    const els = getSkillsElements();
    els.newBtn?.addEventListener('click', startNewSkill);
    els.saveBtn?.addEventListener('click', saveSkillFromEditor);
    els.deleteBtn?.addEventListener('click', deleteSelectedSkill);
}

function initSkillsPage() {
    if (skillsState.initialized) {
        refreshSkillsPanel().catch((e) => console.warn('[SKILLS] refresh failed:', e));
        return;
    }
    bindSkillsHandlers();
    skillsState.initialized = true;
    refreshSkillsPanel().catch((e) => console.warn('[SKILLS] init failed:', e));
}

window.initSkillsPage = initSkillsPage;
