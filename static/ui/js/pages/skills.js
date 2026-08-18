/* MCPO Skills Page — split-panel layout with folder grouping and package installer */

const skillsState = {
    initialized: false,
    list: [],
    listIssues: [],
    listError: '',
    selectedId: null,
    selectedSkill: null,
    isNew: false,
    view: 'empty',
    listRequestId: 0,
    selectionRequestId: 0,
    packageRequestId: 0,
    cliPlanRequestId: 0,
    skillInspectRequestId: 0,
    toolPreviewRequestId: 0,
    cliPlan: null,
    cliPlanInput: null,
    cliPackages: null,
    skillInspection: null,
    skillPackagePayload: null,
    skillPackages: null,
    toolPreview: null,
    collapsedFolders: new Set(),
};

const MAX_SKILL_PACKAGE_BYTES = 10 * 1024 * 1024;

function getSkillsElements() {
    return {
        listContainer: document.getElementById('skills-list'),
        status: document.getElementById('skills-status'),
        emptyState: document.getElementById('skills-empty-state'),
        editor: document.getElementById('skills-editor'),
        editorMode: document.getElementById('skills-editor-mode'),
        editorMeta: document.getElementById('skills-editor-meta'),
        newBtn: document.getElementById('skills-new-btn'),
        installBtn: document.getElementById('skills-install-btn'),
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
        installer: document.getElementById('skills-installer'),
        installerCloseBtn: document.getElementById('skills-installer-close-btn'),
        installerRefreshBtn: document.getElementById('skills-installer-refresh-btn'),
        skillFileInput: document.getElementById('skill-package-file-input'),
        skillInspectBtn: document.getElementById('skill-package-inspect-btn'),
        skillPreview: document.getElementById('skill-package-preview'),
        skillSummary: document.getElementById('skill-package-summary'),
        skillItems: document.getElementById('skill-package-skills'),
        skillWarnings: document.getElementById('skill-package-warnings'),
        skillConfirm: document.getElementById('skill-package-confirm'),
        skillInstallBtn: document.getElementById('skill-package-install-btn'),
        skillPackagesList: document.getElementById('skill-packages-list'),
        cliKind: document.getElementById('cli-package-kind'),
        cliSpec: document.getElementById('cli-package-spec'),
        cliHint: document.getElementById('cli-package-hint'),
        cliAllowScripts: document.getElementById('cli-package-allow-scripts'),
        cliAllowScriptsRow: document.getElementById('cli-allow-scripts-row'),
        cliPlanBtn: document.getElementById('cli-package-plan-btn'),
        cliPreview: document.getElementById('cli-package-preview'),
        cliSummary: document.getElementById('cli-package-summary'),
        cliCommand: document.getElementById('cli-package-command'),
        cliConfirm: document.getElementById('cli-package-confirm'),
        cliInstallBtn: document.getElementById('cli-package-install-btn'),
        cliPackagesList: document.getElementById('cli-packages-list'),
        toolFileInput: document.getElementById('tool-package-file-input'),
        toolPreviewBtn: document.getElementById('tool-package-preview-btn'),
        toolPreview: document.getElementById('tool-package-preview'),
        toolSummary: document.getElementById('tool-package-summary'),
        toolManifests: document.getElementById('tool-package-manifests'),
    };
}

function getErrorMessage(error, fallback) {
    if (error && error.message) return String(error.message);
    return fallback;
}

function setSkillsStatus(message, kind) {
    const status = getSkillsElements().status;
    if (!status) return;
    status.hidden = !message;
    status.className = 'sk-status' + (kind ? ' status-' + kind : '');
    status.textContent = message || '';
}

function setButtonBusy(button, busy, busyLabel) {
    if (!button) return;
    if (busy) {
        button.dataset.idleLabel = button.textContent;
        button.textContent = busyLabel;
        button.disabled = true;
        return;
    }
    button.textContent = button.dataset.idleLabel || button.textContent;
    button.disabled = false;
    delete button.dataset.idleLabel;
}

function sourceLabel(sourceKind) {
    const labels = {
        local: 'Local',
        legacy: 'Legacy',
        installed: 'Installed',
        bundled: 'Bundled',
    };
    return labels[sourceKind] || 'Skill';
}

function formatBytes(value) {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes < 0) return '';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
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
        empty.textContent = skillsState.listError
            ? 'Skills could not be loaded.'
            : 'No skills yet';
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

    const meta = document.createElement('div');
    meta.className = 'sk-card-meta';

    const source = document.createElement('span');
    source.className = 'sk-badge sk-badge-source';
    source.textContent = sourceLabel(skill.sourceKind);
    meta.appendChild(source);

    if (Number.isFinite(Number(skill.resourceCount)) && Number(skill.resourceCount) > 0) {
        const resources = document.createElement('span');
        resources.className = 'sk-badge';
        resources.textContent = Number(skill.resourceCount) + ' resources';
        meta.appendChild(resources);
    }
    if (skill.editable === false) {
        const readOnly = document.createElement('span');
        readOnly.className = 'sk-badge';
        readOnly.textContent = 'Read only';
        meta.appendChild(readOnly);
    }
    info.appendChild(meta);

    const toggle = document.createElement('div');
    toggle.className = 'toggle' + (skill.enabled ? ' on' : '');
    toggle.title = skill.enabled ? 'Enabled. Click to disable.' : 'Disabled. Click to enable.';
    toggle.setAttribute('role', 'switch');
    toggle.setAttribute('tabindex', '0');
    toggle.setAttribute('aria-checked', skill.enabled ? 'true' : 'false');
    toggle.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleSkillEnabled(skill.id, !skill.enabled, toggle);
    });
    toggle.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        toggleSkillEnabled(skill.id, !skill.enabled, toggle);
    });

    card.appendChild(info);
    card.appendChild(toggle);
    return card;
}

// --- Skill selection ---

async function selectSkill(skillId) {
    const els = getSkillsElements();
    const requestId = ++skillsState.selectionRequestId;
    skillsState.selectedId = skillId;
    skillsState.selectedSkill = null;
    skillsState.isNew = false;
    skillsState.view = 'editor';
    renderSkillsList();
    showSkillsView('editor');
    setSkillsStatus('', '');

    els.idInput.value = skillId;
    els.idInput.readOnly = true;
    els.titleInput.value = '';
    els.descInput.value = '';
    els.contentInput.value = '';
    els.titleInput.readOnly = true;
    els.descInput.readOnly = true;
    els.contentInput.readOnly = true;
    els.editorMode.textContent = 'Loading';
    els.editorMeta.textContent = '';
    els.deleteBtn.style.display = 'none';
    els.saveBtn.style.display = 'none';

    let skill;
    try {
        skill = await fetchSkill(skillId);
    } catch (error) {
        if (
            requestId !== skillsState.selectionRequestId
            || skillsState.selectedId !== skillId
            || skillsState.isNew
        ) {
            return;
        }
        els.editorMode.textContent = 'Load failed';
        setSkillsStatus(getErrorMessage(error, 'Failed to load skill.'), 'error');
        return;
    }
    if (
        requestId !== skillsState.selectionRequestId
        || skillsState.selectedId !== skillId
        || skillsState.isNew
    ) {
        return;
    }

    skillsState.selectedSkill = skill;
    const editable = skill.editable === true;
    els.idInput.value = skill.id || '';
    els.idInput.readOnly = true;
    els.titleInput.value = skill.title || '';
    els.descInput.value = skill.description || '';
    els.priorityInput.value = skill.priority ?? 100;
    els.scopesInput.value = arrayToCsv(skill.scopes);
    els.providersInput.value = arrayToCsv(skill.providers);
    els.modelsInput.value = arrayToCsv(skill.models);
    els.contentInput.value = skill.content || '';
    els.titleInput.readOnly = !editable;
    els.descInput.readOnly = !editable;
    els.contentInput.readOnly = !editable;
    els.editorMode.textContent = editable
        ? 'Editing'
        : sourceLabel(skill.sourceKind) + ' read only';

    const metadata = [sourceLabel(skill.sourceKind)];
    if (skill.packageId) metadata.push('Package ' + skill.packageId);
    if (Number.isFinite(Number(skill.resourceCount))) {
        metadata.push(Number(skill.resourceCount) + ' resources');
    }
    els.editorMeta.textContent = metadata.join(' · ');
    els.deleteBtn.style.display = editable ? '' : 'none';
    els.saveBtn.style.display = editable ? '' : 'none';

    if (els.sourcePath) {
        if (skill.sourcePath) {
            els.sourcePath.textContent = skill.sourcePath;
            els.sourcePath.style.display = '';
        } else {
            els.sourcePath.style.display = 'none';
        }
    }
}

function startNewSkill() {
    const els = getSkillsElements();
    skillsState.selectionRequestId += 1;
    skillsState.selectedId = null;
    skillsState.selectedSkill = null;
    skillsState.isNew = true;
    skillsState.view = 'editor';
    renderSkillsList();
    showSkillsView('editor');
    setSkillsStatus('', '');

    els.idInput.value = '';
    els.idInput.readOnly = false;
    els.titleInput.value = '';
    els.descInput.value = '';
    els.priorityInput.value = 100;
    els.scopesInput.value = 'chat, completions';
    els.providersInput.value = '';
    els.modelsInput.value = '';
    els.contentInput.value = '';
    els.titleInput.readOnly = false;
    els.descInput.readOnly = false;
    els.contentInput.readOnly = false;
    els.editorMode.textContent = 'New Skill';
    els.editorMeta.textContent = 'Local · Editable';
    els.deleteBtn.style.display = 'none';
    els.saveBtn.style.display = '';
    if (els.sourcePath) els.sourcePath.style.display = 'none';
    els.idInput.focus();
}

function showSkillsView(view) {
    const els = getSkillsElements();
    skillsState.view = view;
    if (els.editor) els.editor.style.display = view === 'editor' ? '' : 'none';
    if (els.emptyState) els.emptyState.style.display = view === 'empty' ? '' : 'none';
    if (els.installer) els.installer.style.display = view === 'installer' ? '' : 'none';
}

// --- Toggle enable/disable ---

async function toggleSkillEnabled(skillId, enabled, toggleEl) {
    if (toggleEl.getAttribute('aria-disabled') === 'true') return;
    toggleEl.setAttribute('aria-disabled', 'true');
    setSkillsStatus('', '');
    try {
        await setSkillEnabled(skillId, enabled);
        const item = skillsState.list.find((skill) => skill.id === skillId);
        if (item) item.enabled = enabled;
        renderSkillsList();
    } catch (error) {
        setSkillsStatus(
            getErrorMessage(error, 'Failed to change skill state.'),
            'error',
        );
        toggleEl.setAttribute('aria-disabled', 'false');
    }
}

// --- Save ---

async function saveSkillFromEditor() {
    const els = getSkillsElements();
    if (!els.idInput) return;
    if (!skillsState.isNew && skillsState.selectedSkill?.editable !== true) {
        setSkillsStatus('This skill is read only and cannot be changed.', 'error');
        return;
    }
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
    if (!payload.id || !payload.title || !payload.description || !payload.content.trim()) {
        setSkillsStatus('ID, title, description, and content are required.', 'warning');
        return;
    }
    setSkillsStatus('', '');
    setButtonBusy(els.saveBtn, true, 'Saving...');
    try {
        await saveSkill(payload);
        skillsState.selectedId = payload.id;
        skillsState.selectedSkill = null;
        skillsState.isNew = false;
        await refreshSkillsPanel();
        setSkillsStatus('Skill saved.', 'success');
    } catch (error) {
        setSkillsStatus(getErrorMessage(error, 'Failed to save skill.'), 'error');
    } finally {
        setButtonBusy(els.saveBtn, false, '');
        if (
            skillsState.view === 'editor'
            && (skillsState.isNew || skillsState.selectedSkill?.editable === true)
        ) {
            els.saveBtn.style.display = '';
        }
    }
}

// --- Delete ---

async function deleteSelectedSkill() {
    const selectedId = skillsState.selectedId;
    const selectedSkill = skillsState.selectedSkill;
    if (!selectedId) return;
    if (!selectedSkill || selectedSkill.editable !== true) {
        setSkillsStatus('This skill is read only and cannot be deleted.', 'error');
        return;
    }
    const name = selectedSkill.title || selectedId;
    if (!confirm('Delete skill "' + name + '"? This cannot be undone.')) return;

    const els = getSkillsElements();
    setButtonBusy(els.deleteBtn, true, 'Deleting...');
    setSkillsStatus('', '');
    try {
        await deleteSkill(selectedId);
        if (skillsState.selectedId === selectedId) {
            skillsState.selectionRequestId += 1;
            skillsState.selectedId = null;
            skillsState.selectedSkill = null;
            skillsState.isNew = false;
            showSkillsView('empty');
        }
        await refreshSkillsPanel();
        setSkillsStatus('Skill deleted.', 'success');
    } catch (error) {
        setSkillsStatus(getErrorMessage(error, 'Failed to delete skill.'), 'error');
    } finally {
        setButtonBusy(els.deleteBtn, false, '');
    }
}

// --- Refresh ---

async function refreshSkillsPanel() {
    const requestId = ++skillsState.listRequestId;
    let catalog;
    try {
        catalog = await fetchSkillsCatalog();
    } catch (error) {
        if (requestId !== skillsState.listRequestId) return false;
        skillsState.listError = getErrorMessage(error, 'Failed to load skills.');
        renderSkillsList();
        setSkillsStatus(skillsState.listError, 'error');
        return false;
    }
    if (requestId !== skillsState.listRequestId) return false;

    const skills = catalog.skills;
    skillsState.list = skills;
    skillsState.listIssues = catalog.issues;
    skillsState.listError = '';
    renderSkillsList();
    if (catalog.issues.length) {
        const firstMessage = catalog.issues[0]?.message || 'Invalid skill package';
        setSkillsStatus(
            catalog.issues.length + ' skill package issue(s): ' + firstMessage,
            'warning',
        );
    }

    if (
        skillsState.selectedId
        && !skills.some((skill) => skill.id === skillsState.selectedId)
    ) {
        skillsState.selectionRequestId += 1;
        skillsState.selectedId = null;
        skillsState.selectedSkill = null;
        skillsState.isNew = false;
        if (skillsState.view !== 'installer') showSkillsView('empty');
        return true;
    }
    if (
        skillsState.selectedId
        && skillsState.view === 'editor'
        && !skillsState.isNew
    ) {
        await selectSkill(skillsState.selectedId);
    }
    return true;
}

// --- Package installer ---

async function openSkillsInstaller() {
    skillsState.selectionRequestId += 1;
    showSkillsView('installer');
    setSkillsStatus('', '');
    await refreshInstallerPackages();
}

function closeSkillsInstaller() {
    if (skillsState.isNew) {
        showSkillsView('editor');
        return;
    }
    if (skillsState.selectedId) {
        selectSkill(skillsState.selectedId);
        return;
    }
    showSkillsView('empty');
}

function renderListMessage(container, message) {
    if (!container) return;
    container.innerHTML = '';
    const empty = document.createElement('div');
    empty.className = 'sk-list-empty';
    empty.textContent = message;
    container.appendChild(empty);
}

function createPackageRow(name, details, onUninstall) {
    const row = document.createElement('div');
    row.className = 'sk-package-row';

    const info = document.createElement('div');
    info.className = 'sk-package-info';
    const title = document.createElement('div');
    title.className = 'sk-package-name';
    title.textContent = name;
    const detail = document.createElement('div');
    detail.className = 'sk-package-detail';
    detail.textContent = details.filter(Boolean).join(' · ');
    info.appendChild(title);
    info.appendChild(detail);

    const uninstall = document.createElement('button');
    uninstall.className = 'btn btn-danger';
    uninstall.type = 'button';
    uninstall.textContent = 'Uninstall';
    uninstall.addEventListener('click', () => onUninstall(uninstall));

    row.appendChild(info);
    row.appendChild(uninstall);
    return row;
}

function renderCliPackages() {
    const container = getSkillsElements().cliPackagesList;
    if (!container) return;
    if (skillsState.cliPackages === null) {
        renderListMessage(container, 'Loading packages...');
        return;
    }
    if (!skillsState.cliPackages.length) {
        renderListMessage(container, 'No managed CLI packages installed.');
        return;
    }

    container.innerHTML = '';
    skillsState.cliPackages.forEach((pkg) => {
        const displayName = pkg.name
            ? pkg.name + (pkg.version ? ' ' + pkg.version : '')
            : (pkg.spec || pkg.packageId);
        const details = [
            pkg.kind ? String(pkg.kind).toUpperCase() : '',
            pkg.spec || '',
            pkg.status || '',
            pkg.isolation || '',
        ];
        if (Array.isArray(pkg.executables) && pkg.executables.length) {
            const executableNames = pkg.executables
                .map((executable) => executable && executable.name)
                .filter(Boolean);
            if (executableNames.length) {
                details.push('Commands: ' + executableNames.join(', '));
            }
        }
        if (pkg.installDir) details.push(pkg.installDir);
        container.appendChild(
            createPackageRow(
                displayName,
                details,
                (button) => uninstallCliPackageFromUi(pkg, button),
            ),
        );
    });
}

function renderSkillPackages() {
    const container = getSkillsElements().skillPackagesList;
    if (!container) return;
    if (skillsState.skillPackages === null) {
        renderListMessage(container, 'Loading packages...');
        return;
    }
    if (!skillsState.skillPackages.length) {
        renderListMessage(container, 'No skill packages installed.');
        return;
    }

    container.innerHTML = '';
    skillsState.skillPackages.forEach((pkg) => {
        const skillNames = Array.isArray(pkg.skills)
            ? pkg.skills.map((skill) => skill.title || skill.id).filter(Boolean)
            : [];
        const details = [];
        if (skillNames.length) details.push('Skills: ' + skillNames.join(', '));
        if (Number.isFinite(Number(pkg.fileCount))) {
            details.push(Number(pkg.fileCount) + ' files');
        }
        const size = formatBytes(pkg.totalBytes);
        if (size) details.push(size);
        container.appendChild(
            createPackageRow(
                pkg.filename || pkg.id,
                details,
                (button) => uninstallSkillPackageFromUi(pkg, button),
            ),
        );
    });
}

async function refreshInstallerPackages() {
    const requestId = ++skillsState.packageRequestId;
    const els = getSkillsElements();
    if (skillsState.cliPackages === null) {
        renderListMessage(els.cliPackagesList, 'Loading packages...');
    }
    if (skillsState.skillPackages === null) {
        renderListMessage(els.skillPackagesList, 'Loading packages...');
    }

    const results = await Promise.allSettled([
        fetchCliPackages(),
        fetchSkillPackages(),
    ]);
    if (requestId !== skillsState.packageRequestId) return;

    const errors = [];
    if (results[0].status === 'fulfilled') {
        skillsState.cliPackages = results[0].value;
        renderCliPackages();
    } else {
        const message = getErrorMessage(
            results[0].reason,
            'Failed to load installed CLI packages.',
        );
        renderListMessage(els.cliPackagesList, message);
        errors.push(message);
    }

    if (results[1].status === 'fulfilled') {
        skillsState.skillPackages = results[1].value;
        renderSkillPackages();
    } else {
        const message = getErrorMessage(
            results[1].reason,
            'Failed to load installed skill packages.',
        );
        renderListMessage(els.skillPackagesList, message);
        errors.push(message);
    }

    if (errors.length) {
        setSkillsStatus(errors.join(' '), 'error');
    }
}

async function uninstallCliPackageFromUi(pkg, button) {
    const name = pkg.name || pkg.spec || pkg.packageId;
    if (!confirm('Uninstall CLI package "' + name + '"?')) return;
    setButtonBusy(button, true, 'Removing...');
    setSkillsStatus('', '');
    try {
        await uninstallCliPackage(pkg.packageId, true);
        resetToolManifestPreview();
        skillsState.cliPackages = null;
        await refreshInstallerPackages();
        setSkillsStatus('CLI package uninstalled.', 'success');
    } catch (error) {
        setSkillsStatus(
            getErrorMessage(error, 'Failed to uninstall CLI package.'),
            'error',
        );
    } finally {
        setButtonBusy(button, false, '');
    }
}

async function uninstallSkillPackageFromUi(pkg, button) {
    const name = pkg.filename || pkg.id;
    if (!confirm('Uninstall skill package "' + name + '"?')) return;
    setButtonBusy(button, true, 'Removing...');
    setSkillsStatus('', '');
    try {
        await uninstallSkillPackage(pkg.id, true);
        skillsState.skillPackages = null;
        await Promise.all([
            refreshInstallerPackages(),
            refreshSkillsPanel(),
        ]);
        setSkillsStatus('Skill package uninstalled.', 'success');
    } catch (error) {
        setSkillsStatus(
            getErrorMessage(error, 'Failed to uninstall skill package.'),
            'error',
        );
    } finally {
        setButtonBusy(button, false, '');
    }
}

function getCliInstallInput(confirmed) {
    const els = getSkillsElements();
    const kind = els.cliKind.value === 'npm' ? 'npm' : 'python';
    return {
        kind,
        spec: (els.cliSpec.value || '').trim(),
        allow_scripts: kind === 'npm' && els.cliAllowScripts.checked,
        confirmed: confirmed === true,
    };
}

function cliInputsMatch(left, right) {
    return !!left
        && !!right
        && left.kind === right.kind
        && left.spec === right.spec
        && left.allow_scripts === right.allow_scripts;
}

function invalidateCliPlan() {
    const els = getSkillsElements();
    skillsState.cliPlanRequestId += 1;
    skillsState.cliPlan = null;
    skillsState.cliPlanInput = null;
    if (els.cliPreview) els.cliPreview.hidden = true;
    if (els.cliConfirm) els.cliConfirm.checked = false;
    if (els.cliConfirm) els.cliConfirm.disabled = false;
    if (els.cliInstallBtn) els.cliInstallBtn.disabled = true;
}

function updateCliKindUi() {
    const els = getSkillsElements();
    const isNpm = els.cliKind.value === 'npm';
    els.cliAllowScripts.checked = false;
    els.cliAllowScripts.disabled = !isNpm;
    els.cliAllowScriptsRow.classList.toggle('sk-confirm-muted', !isNpm);
    els.cliSpec.placeholder = isNpm ? 'your-cli@1.2.3' : 'your-cli==1.2.3';
    els.cliHint.textContent = isNpm
        ? 'Use an exact version such as your-cli@1.2.3.'
        : 'Use an exact version such as your-cli==1.2.3. Python packages require a compatible binary wheel.';
    invalidateCliPlan();
}

function renderCliPlan() {
    const els = getSkillsElements();
    const plan = skillsState.cliPlan;
    if (!plan) {
        els.cliPreview.hidden = true;
        return;
    }

    els.cliSummary.innerHTML = '';
    const title = document.createElement('div');
    title.className = 'sk-review-title';
    title.textContent = (plan.name || plan.spec || plan.packageId)
        + (plan.version ? ' ' + plan.version : '');
    const metadata = document.createElement('div');
    metadata.className = 'sk-review-meta';
    const details = [
        plan.kind ? String(plan.kind).toUpperCase() : '',
        plan.status || '',
        plan.isolation || '',
        plan.installDir || plan.root || '',
    ];
    if (plan.exists) details.push('Already installed');
    if (plan.allowScripts) details.push('npm scripts allowed');
    metadata.textContent = details.filter(Boolean).join(' · ');
    els.cliSummary.appendChild(title);
    els.cliSummary.appendChild(metadata);

    els.cliCommand.textContent = [
        plan.root ? 'Root: ' + plan.root : '',
        plan.recordDir ? 'Record: ' + plan.recordDir : '',
        plan.installDir ? 'Install: ' + plan.installDir : '',
        plan.manifestPath ? 'Manifest: ' + plan.manifestPath : '',
    ].filter(Boolean).join('\n');
    els.cliConfirm.checked = false;
    els.cliConfirm.disabled = plan.exists === true;
    els.cliInstallBtn.disabled = true;
    els.cliPreview.hidden = false;
}

async function reviewCliPackagePlan() {
    const els = getSkillsElements();
    const input = getCliInstallInput(false);
    if (!input.spec) {
        setSkillsStatus('Enter a version-pinned CLI package.', 'warning');
        return;
    }

    const requestId = ++skillsState.cliPlanRequestId;
    setButtonBusy(els.cliPlanBtn, true, 'Reviewing...');
    setSkillsStatus('', '');
    try {
        const plan = await planCliPackage(input);
        const currentInput = getCliInstallInput(false);
        if (
            requestId !== skillsState.cliPlanRequestId
            || !cliInputsMatch(input, currentInput)
        ) {
            return;
        }
        skillsState.cliPlan = plan;
        skillsState.cliPlanInput = input;
        renderCliPlan();
    } catch (error) {
        if (requestId !== skillsState.cliPlanRequestId) return;
        setSkillsStatus(
            getErrorMessage(error, 'Failed to review CLI install plan.'),
            'error',
        );
    } finally {
        setButtonBusy(els.cliPlanBtn, false, '');
    }
}

async function installCliPackageFromUi() {
    const els = getSkillsElements();
    const currentInput = getCliInstallInput(false);
    if (
        !skillsState.cliPlan
        || !cliInputsMatch(skillsState.cliPlanInput, currentInput)
    ) {
        invalidateCliPlan();
        setSkillsStatus('Review the current CLI install plan first.', 'warning');
        return;
    }
    if (!els.cliConfirm.checked) {
        setSkillsStatus('Confirm the reviewed CLI install plan first.', 'warning');
        return;
    }

    setButtonBusy(els.cliInstallBtn, true, 'Installing...');
    setSkillsStatus('', '');
    try {
        const installed = await installCliPackage({
            ...currentInput,
            confirmed: true,
        });
        invalidateCliPlan();
        resetToolManifestPreview();
        els.cliAllowScripts.checked = false;
        skillsState.cliPackages = null;
        await refreshInstallerPackages();
        setSkillsStatus(
            'Installed CLI package '
                + (installed.name || installed.spec || installed.packageId)
                + '.',
            'success',
        );
    } catch (error) {
        els.cliConfirm.checked = false;
        els.cliInstallBtn.disabled = true;
        setSkillsStatus(
            getErrorMessage(error, 'Failed to install CLI package.'),
            'error',
        );
    } finally {
        setButtonBusy(els.cliInstallBtn, false, '');
        els.cliInstallBtn.disabled = (
            !skillsState.cliPlan
            || !els.cliConfirm.checked
            || skillsState.cliPlan.exists === true
        );
    }
}

function resetSkillInspection() {
    const els = getSkillsElements();
    skillsState.skillInspectRequestId += 1;
    skillsState.skillInspection = null;
    skillsState.skillPackagePayload = null;
    if (els.skillPreview) els.skillPreview.hidden = true;
    if (els.skillConfirm) els.skillConfirm.checked = false;
    if (els.skillInstallBtn) els.skillInstallBtn.disabled = true;
}

function selectedFileKey(file) {
    if (!file) return '';
    return [file.name, file.size, file.lastModified].join(':');
}

function readFileAsBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            const value = typeof reader.result === 'string' ? reader.result : '';
            const separator = value.indexOf(',');
            if (separator < 0) {
                reject(new Error('Could not encode the selected package.'));
                return;
            }
            resolve(value.slice(separator + 1));
        };
        reader.onerror = () => reject(
            reader.error || new Error('Could not read the selected package.'),
        );
        reader.readAsDataURL(file);
    });
}

function renderSkillInspection() {
    const els = getSkillsElements();
    const inspection = skillsState.skillInspection;
    if (!inspection) {
        els.skillPreview.hidden = true;
        return;
    }

    els.skillSummary.innerHTML = '';
    const title = document.createElement('div');
    title.className = 'sk-review-title';
    title.textContent = inspection.filename || inspection.packageId;
    const metadata = document.createElement('div');
    metadata.className = 'sk-review-meta';
    const details = [];
    if (Number.isFinite(Number(inspection.fileCount))) {
        details.push(Number(inspection.fileCount) + ' files');
    }
    const size = formatBytes(inspection.totalBytes);
    if (size) details.push(size);
    if (inspection.destination) details.push(inspection.destination);
    if (inspection.installed) details.push('Already installed');
    metadata.textContent = details.join(' · ');
    els.skillSummary.appendChild(title);
    els.skillSummary.appendChild(metadata);

    els.skillItems.innerHTML = '';
    const inspectedSkills = Array.isArray(inspection.skills) ? inspection.skills : [];
    inspectedSkills.forEach((skill) => {
        const badge = document.createElement('span');
        badge.className = 'sk-badge';
        badge.textContent = skill.title || skill.id;
        badge.title = skill.description || skill.id || '';
        els.skillItems.appendChild(badge);
    });

    const warnings = Array.isArray(inspection.warnings)
        ? inspection.warnings.map(String)
        : [];
    if (inspection.hasScripts && !warnings.some((warning) => /script/i.test(warning))) {
        warnings.unshift('This archive contains scripts. Review its source before installing.');
    }
    const conflicts = Array.isArray(inspection.conflicts) ? inspection.conflicts : [];
    if (inspection.installed) {
        warnings.unshift('This package is already installed.');
    }
    const blocked = inspection.installed === true || conflicts.length > 0;
    els.skillWarnings.textContent = warnings.join(' ');
    els.skillWarnings.hidden = warnings.length === 0;
    els.skillConfirm.checked = false;
    els.skillConfirm.disabled = blocked;
    els.skillInstallBtn.disabled = true;
    els.skillPreview.hidden = false;
}

async function inspectSelectedSkillPackage() {
    const els = getSkillsElements();
    const file = els.skillFileInput.files && els.skillFileInput.files[0];
    if (!file) {
        setSkillsStatus('Choose a .skill or .zip package first.', 'warning');
        return;
    }
    const lowerName = file.name.toLowerCase();
    if (!lowerName.endsWith('.skill') && !lowerName.endsWith('.zip')) {
        setSkillsStatus('Only .skill and .zip packages can be inspected.', 'warning');
        return;
    }
    if (file.size > MAX_SKILL_PACKAGE_BYTES) {
        setSkillsStatus('Skill packages must be 10 MB or smaller.', 'warning');
        return;
    }

    resetSkillInspection();
    const requestId = ++skillsState.skillInspectRequestId;
    const fileKey = selectedFileKey(file);
    setButtonBusy(els.skillInspectBtn, true, 'Inspecting...');
    setSkillsStatus('', '');
    try {
        const contentBase64 = await readFileAsBase64(file);
        if (
            requestId !== skillsState.skillInspectRequestId
            || selectedFileKey(els.skillFileInput.files[0]) !== fileKey
        ) {
            return;
        }
        const payload = {
            filename: file.name,
            content_base64: contentBase64,
            confirmed: false,
        };
        const inspection = await inspectSkillPackage(payload);
        if (
            requestId !== skillsState.skillInspectRequestId
            || selectedFileKey(els.skillFileInput.files[0]) !== fileKey
        ) {
            return;
        }
        skillsState.skillPackagePayload = {
            filename: payload.filename,
            content_base64: payload.content_base64,
        };
        skillsState.skillInspection = inspection;
        renderSkillInspection();
    } catch (error) {
        if (requestId !== skillsState.skillInspectRequestId) return;
        setSkillsStatus(
            getErrorMessage(error, 'Failed to inspect skill package.'),
            'error',
        );
    } finally {
        setButtonBusy(els.skillInspectBtn, false, '');
    }
}

async function installSkillPackageFromUi() {
    const els = getSkillsElements();
    if (!skillsState.skillInspection || !skillsState.skillPackagePayload) {
        setSkillsStatus('Inspect the selected skill package first.', 'warning');
        return;
    }
    if (!els.skillConfirm.checked) {
        setSkillsStatus('Confirm the inspected skill package first.', 'warning');
        return;
    }

    setButtonBusy(els.skillInstallBtn, true, 'Installing...');
    setSkillsStatus('', '');
    try {
        const installed = await installSkillPackage({
            ...skillsState.skillPackagePayload,
            confirmed: true,
        });
        resetSkillInspection();
        els.skillFileInput.value = '';
        skillsState.skillPackages = null;
        await Promise.all([
            refreshInstallerPackages(),
            refreshSkillsPanel(),
        ]);
        setSkillsStatus(
            'Installed skill package ' + (installed.filename || installed.id) + '.',
            'success',
        );
    } catch (error) {
        els.skillConfirm.checked = false;
        els.skillInstallBtn.disabled = true;
        setSkillsStatus(
            getErrorMessage(error, 'Failed to install skill package.'),
            'error',
        );
    } finally {
        setButtonBusy(els.skillInstallBtn, false, '');
        const conflicts = Array.isArray(skillsState.skillInspection?.conflicts)
            ? skillsState.skillInspection.conflicts
            : [];
        els.skillInstallBtn.disabled = (
            !skillsState.skillInspection
            || !els.skillConfirm.checked
            || skillsState.skillInspection.installed === true
            || conflicts.length > 0
        );
    }
}

function resetToolManifestPreview() {
    const els = getSkillsElements();
    skillsState.toolPreviewRequestId += 1;
    skillsState.toolPreview = null;
    if (els.toolPreviewBtn) setButtonBusy(els.toolPreviewBtn, false, '');
    if (els.toolPreview) els.toolPreview.hidden = true;
    if (els.toolSummary) els.toolSummary.innerHTML = '';
    if (els.toolManifests) els.toolManifests.innerHTML = '';
}

function renderToolManifestPreview() {
    const els = getSkillsElements();
    const preview = skillsState.toolPreview;
    if (!preview || !els.toolPreview || !els.toolSummary || !els.toolManifests) {
        if (els.toolPreview) els.toolPreview.hidden = true;
        return;
    }

    els.toolSummary.innerHTML = '';
    const title = document.createElement('div');
    title.className = 'sk-review-title';
    title.textContent = preview.filename || 'Tool proposal';
    const metadata = document.createElement('div');
    metadata.className = 'sk-review-meta';
    const details = [];
    if (Number.isFinite(Number(preview.manifestCount))) {
        details.push(Number(preview.manifestCount) + ' TOOL.yaml');
    }
    if (Number.isFinite(Number(preview.fileCount))) {
        details.push(Number(preview.fileCount) + ' files');
    }
    const size = formatBytes(preview.totalBytes);
    if (size) details.push(size);
    details.push(preview.valid === true ? 'Schema valid' : 'Schema errors');
    details.push(preview.resolved === true ? 'Dependencies resolved' : 'Dependencies unresolved');
    metadata.textContent = details.join(' · ');
    const archiveHash = document.createElement('code');
    archiveHash.className = 'sk-command-preview';
    archiveHash.textContent = 'Archive SHA-256: ' + String(preview.archiveSha256 || '');
    els.toolSummary.appendChild(title);
    els.toolSummary.appendChild(metadata);
    els.toolSummary.appendChild(archiveHash);

    els.toolManifests.innerHTML = '';
    const manifests = Array.isArray(preview.manifests) ? preview.manifests : [];
    manifests.forEach((manifest) => {
        const card = document.createElement('div');
        card.className = 'sk-review';

        const manifestTitle = document.createElement('div');
        manifestTitle.className = 'sk-review-title';
        manifestTitle.textContent = String(manifest.path || 'TOOL.yaml');
        card.appendChild(manifestTitle);

        const manifestMeta = document.createElement('div');
        manifestMeta.className = 'sk-review-meta';
        const manifestStatus = [
            manifest.valid === true ? 'Valid' : 'Invalid',
            manifest.resolved === true ? 'Resolved' : 'Not resolved',
            'Execution disabled',
        ];
        manifestMeta.textContent = manifestStatus.join(' · ');
        card.appendChild(manifestMeta);

        const manifestHash = document.createElement('code');
        manifestHash.className = 'sk-command-preview';
        manifestHash.textContent = (
            'Manifest SHA-256: ' + String(manifest.manifestSha256 || '')
            + (manifest.normalizedSha256
                ? '\nNormalized SHA-256: ' + String(manifest.normalizedSha256)
                : '')
        );
        card.appendChild(manifestHash);

        const dependency = manifest.dependency && typeof manifest.dependency === 'object'
            ? manifest.dependency
            : null;
        if (dependency) {
            const dependencyLine = document.createElement('div');
            dependencyLine.className = 'sk-review-meta';
            dependencyLine.textContent = (
                'Dependency: ' + String(dependency.status || 'unknown')
                + (dependency.message ? ' · ' + String(dependency.message) : '')
            );
            card.appendChild(dependencyLine);
        }

        const errors = Array.isArray(manifest.errors) ? manifest.errors : [];
        const warnings = Array.isArray(manifest.warnings) ? manifest.warnings : [];
        const issueMessages = [...errors, ...warnings].map((issue) => {
            if (!issue || typeof issue !== 'object') return String(issue || '');
            const path = issue.path ? String(issue.path) + ': ' : '';
            return path + String(issue.message || issue.code || 'Manifest issue');
        }).filter(Boolean);
        if (issueMessages.length) {
            const issues = document.createElement('div');
            issues.className = 'sk-review-warnings';
            issues.textContent = issueMessages.join(' ');
            card.appendChild(issues);
        }

        if (Array.isArray(manifest.commandPreview) && manifest.commandPreview.length) {
            const command = document.createElement('code');
            command.className = 'sk-command-preview';
            command.textContent = JSON.stringify(manifest.commandPreview);
            card.appendChild(command);
        }
        if (manifest.normalized && typeof manifest.normalized === 'object') {
            const normalized = document.createElement('code');
            normalized.className = 'sk-command-preview';
            normalized.textContent = JSON.stringify(manifest.normalized, null, 2);
            card.appendChild(normalized);
        }

        const blockers = document.createElement('div');
        blockers.className = 'sk-review-warnings';
        blockers.textContent = 'Blocked: ' + (
            Array.isArray(manifest.blockers)
                ? manifest.blockers.map(String).join(', ')
                : 'execution unavailable'
        );
        card.appendChild(blockers);
        els.toolManifests.appendChild(card);
    });
    els.toolPreview.hidden = false;
}

async function previewSelectedToolPackage() {
    const els = getSkillsElements();
    const file = els.toolFileInput.files && els.toolFileInput.files[0];
    if (!file) {
        setSkillsStatus('Choose a .skill or .zip tool proposal first.', 'warning');
        return;
    }
    const lowerName = file.name.toLowerCase();
    if (!lowerName.endsWith('.skill') && !lowerName.endsWith('.zip')) {
        setSkillsStatus('Only .skill and .zip tool proposals can be previewed.', 'warning');
        return;
    }
    if (file.size > MAX_SKILL_PACKAGE_BYTES) {
        setSkillsStatus('Tool proposal archives must be 10 MB or smaller.', 'warning');
        return;
    }

    resetToolManifestPreview();
    const requestId = ++skillsState.toolPreviewRequestId;
    const fileKey = selectedFileKey(file);
    setButtonBusy(els.toolPreviewBtn, true, 'Previewing...');
    setSkillsStatus('', '');
    try {
        const contentBase64 = await readFileAsBase64(file);
        if (
            requestId !== skillsState.toolPreviewRequestId
            || selectedFileKey(els.toolFileInput.files[0]) !== fileKey
        ) {
            return;
        }
        const preview = await previewToolManifestArchive({
            filename: file.name,
            content_base64: contentBase64,
        });
        if (
            requestId !== skillsState.toolPreviewRequestId
            || selectedFileKey(els.toolFileInput.files[0]) !== fileKey
        ) {
            return;
        }
        skillsState.toolPreview = preview;
        renderToolManifestPreview();
        const message = preview.valid !== true
            ? 'Preview complete with TOOL.yaml errors. Execution remains disabled.'
            : preview.resolved !== true
                ? 'Manifest is valid but a CLI dependency is unresolved. Execution remains disabled.'
                : 'Manifest and CLI entry point resolved. Execution remains disabled.';
        setSkillsStatus(
            message,
            preview.valid === true && preview.resolved === true ? 'success' : 'warning',
        );
    } catch (error) {
        if (requestId !== skillsState.toolPreviewRequestId) return;
        setSkillsStatus(
            getErrorMessage(error, 'Failed to preview tool proposal.'),
            'error',
        );
    } finally {
        if (requestId === skillsState.toolPreviewRequestId) {
            setButtonBusy(els.toolPreviewBtn, false, '');
        }
    }
}

// --- Bind & Init ---

function bindSkillsHandlers() {
    const els = getSkillsElements();
    els.newBtn?.addEventListener('click', startNewSkill);
    els.installBtn?.addEventListener('click', openSkillsInstaller);
    els.saveBtn?.addEventListener('click', saveSkillFromEditor);
    els.deleteBtn?.addEventListener('click', deleteSelectedSkill);
    els.installerCloseBtn?.addEventListener('click', closeSkillsInstaller);
    els.installerRefreshBtn?.addEventListener('click', () => {
        setSkillsStatus('', '');
        refreshInstallerPackages();
    });

    els.cliKind?.addEventListener('change', updateCliKindUi);
    els.cliSpec?.addEventListener('input', invalidateCliPlan);
    els.cliAllowScripts?.addEventListener('change', invalidateCliPlan);
    els.cliPlanBtn?.addEventListener('click', reviewCliPackagePlan);
    els.cliConfirm?.addEventListener('change', () => {
        els.cliInstallBtn.disabled = (
            !els.cliConfirm.checked
            || !skillsState.cliPlan
            || skillsState.cliPlan.exists === true
        );
    });
    els.cliInstallBtn?.addEventListener('click', installCliPackageFromUi);

    els.skillFileInput?.addEventListener('change', () => {
        resetSkillInspection();
        setSkillsStatus('', '');
    });
    els.skillInspectBtn?.addEventListener('click', inspectSelectedSkillPackage);
    els.skillConfirm?.addEventListener('change', () => {
        const conflicts = Array.isArray(skillsState.skillInspection?.conflicts)
            ? skillsState.skillInspection.conflicts
            : [];
        els.skillInstallBtn.disabled = (
            !els.skillConfirm.checked
            || !skillsState.skillInspection
            || skillsState.skillInspection.installed === true
            || conflicts.length > 0
        );
    });
    els.skillInstallBtn?.addEventListener('click', installSkillPackageFromUi);

    els.toolFileInput?.addEventListener('change', () => {
        resetToolManifestPreview();
        setSkillsStatus('', '');
    });
    els.toolPreviewBtn?.addEventListener('click', previewSelectedToolPackage);

    updateCliKindUi();
}

function initSkillsPage() {
    if (skillsState.initialized) {
        refreshSkillsPanel();
        if (skillsState.view === 'installer') refreshInstallerPackages();
        return;
    }
    bindSkillsHandlers();
    skillsState.initialized = true;
    refreshSkillsPanel();
}

window.initSkillsPage = initSkillsPage;
