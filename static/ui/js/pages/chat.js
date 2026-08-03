/* MCPO Chat Page Logic - OpenRouter Agentic Chat */

const CHAT_SESSION_STORAGE_KEY = 'mcpo-chat-session-id';
const FAVORITES_STORAGE_KEY = 'mcpo-chat-favorite-models';
const MODEL_SEARCH_STORAGE_KEY = 'mcpo-chat-model-search';
const PROVIDER_FILTER_STORAGE_KEY = 'mcpo-chat-provider-filter';
const MAX_CHAT_ATTACHMENTS = 8;
const MAX_CHAT_ATTACHMENT_BYTES = 5 * 1024 * 1024;
const MAX_CHAT_ATTACHMENT_TOTAL_BYTES = 20 * 1024 * 1024;
const IMAGE_MIME_TYPES = new Set(['image/jpeg', 'image/png', 'image/gif', 'image/webp']);
const TEXT_MIME_TYPES = new Set([
    'application/json',
    'application/ld+json',
    'application/xml',
    'application/yaml',
    'application/x-yaml',
    'application/javascript',
]);

const chatState = {
    initialized: false,
    initializing: false,
    sessionId: null,
    session: null,
    models: [],
    selectedModelKey: '',
    providerFilter: 'all',
    modelFilter: 'all',
    skills: [],
    selectedSkillIds: [],
    skillSelectionExplicit: false,
    favorites: [],
    modelSearch: '',
    servers: [],
    serverCatalogLoaded: false,
    serverCatalogError: '',
    serverSelectionExplicit: false,
    serverAllowlist: [],
    attachments: [],
    chatSettings: {
        defaultSystemPrompt: '',
        temperature: null,
        maxOutputTokens: 8192,
        maxToolRounds: 8,
        includeReasoning: true,
        reasoningEffort: null,
        includeManagementTools: false,
    },
    streaming: false,
    abortController: null,
    buffer: '',
    stepMessages: {},
    currentStepId: null,
    modelPanelOpen: false,
    // UI state that should persist across re-renders
    expandedReasoningIds: new Set(),  // Track which reasoning blocks are expanded
    expandedToolIds: new Set(),       // Track which tool cards are expanded
};

function getChatElements() {
    return {
        modelSelect: document.getElementById('chat-model-select'),
        modelTrigger: document.getElementById('chat-model-trigger'),
        selectedModelLabel: document.getElementById('chat-model-selected-label'),
        selectedModelProvider: document.getElementById('chat-model-selected-provider'),
        modelList: document.getElementById('chat-model-list'),
        modelListItems: document.getElementById('chat-model-groups'),
        modelListStatus: document.getElementById('chat-model-list-status'),
        modelSearchInput: document.getElementById('chat-model-search'),
        providerFilterSelect: document.getElementById('chat-provider-filter'),
        modelFavToggle: document.getElementById('chat-model-fav-toggle'),
        modelFilterButtons: Array.from(document.querySelectorAll('[data-model-filter]')),
        skillToggle: document.getElementById('chat-skill-toggle'),
        skillList: document.getElementById('chat-skill-list'),
        skillListItems: document.getElementById('chat-skill-list-items'),
        activeSkills: document.getElementById('chat-active-skills'),
        resetBtn: document.getElementById('chat-reset-session'),
        newSessionBtn: document.getElementById('chat-new-session'),
        messagesContainer: document.getElementById('chat-messages'),
        alertsContainer: document.getElementById('chat-alerts'),
        form: document.getElementById('chat-input-form'),
        textarea: document.getElementById('chat-input-text'),
        streamToggle: document.getElementById('chat-stream-toggle'),
        stopBtn: document.getElementById('chat-stop-stream'),
        sendBtn: document.getElementById('chat-send-button'),
        sessionMeta: document.getElementById('chat-session-meta'),
        attachmentInput: document.getElementById('chat-attachment-input'),
        attachmentPreview: document.getElementById('chat-attachment-preview'),
        temperatureInput: document.getElementById('chat-temperature'),
        maxOutputInput: document.getElementById('chat-max-output-tokens'),
        maxToolRoundsInput: document.getElementById('chat-max-tool-rounds'),
        includeReasoningInput: document.getElementById('chat-include-reasoning'),
        reasoningEffortSelect: document.getElementById('chat-reasoning-effort'),
        modelCapabilities: document.getElementById('chat-model-capabilities'),
        includeManagementTools: document.getElementById('chat-include-management-tools'),
        allServers: document.getElementById('chat-all-servers'),
        serverList: document.getElementById('chat-server-list'),
        refreshToolsBtn: document.getElementById('chat-refresh-tools'),
        toolCount: document.getElementById('chat-tool-count'),
    };
}

async function initChatPage() {
    if (chatState.initializing) {
        return;
    }
    chatState.initializing = true;

    const els = getChatElements();
    if (!els.modelTrigger || !els.form) {
        console.warn('[CHAT] Page elements missing; abort init');
        chatState.initializing = false;
        return;
    }

    if (!chatState.initialized) {
        attachEventHandlers(els);
        chatState.initialized = true;
    }
    try {
        await Promise.all([
            loadChatSettings(),
            loadModels(),
            loadSkills(),
            loadFavorites(),
            loadChatServers(),
        ]);
        normalizeFavoriteKeys();
        renderProviderFilter();
        populateModelSelect();
        renderModelList();
        renderSkillList();
        renderChatServerList();
        applyAgentSettingsToControls();
        await ensureSession();
        syncProviderFilterToSelectedModel();
        applyAgentSettingsToControls();
        renderSession();
    } catch (error) {
        console.error('[CHAT] Initialization failed', error);
        showChatAlert(getChatErrorMessage(error, 'Chat initialization failed'), 'error');
    } finally {
        chatState.initializing = false;
    }
}

function attachEventHandlers(els) {
    els.modelTrigger.addEventListener('click', () => {
        setModelPanelOpen(!chatState.modelPanelOpen);
    });

    els.providerFilterSelect?.addEventListener('change', handleProviderFilterChange);

    if (els.resetBtn) {
        els.resetBtn.addEventListener('click', async () => {
            await resetSession();
        });
    }

    if (els.newSessionBtn) {
        els.newSessionBtn.addEventListener('click', async () => {
            await startNewSession();
        });
    }

    if (els.modelFavToggle) {
        els.modelFavToggle.addEventListener('click', () => {
            chatState.modelFilter = 'favorites';
            setModelPanelOpen(true);
        });
    }

    els.modelFilterButtons.forEach((button) => {
        button.addEventListener('click', () => {
            chatState.modelFilter = button.dataset.modelFilter || 'all';
            renderModelList();
        });
    });

    if (els.skillToggle) {
        els.skillToggle.addEventListener('click', () => {
            if (!els.skillList) return;
            els.skillList.classList.toggle('hidden');
            els.skillToggle.setAttribute(
                'aria-expanded',
                String(!els.skillList.classList.contains('hidden')),
            );
        });
    }

    if (els.modelSearchInput) {
        els.modelSearchInput.addEventListener('input', (e) => {
            chatState.modelSearch = e.target.value || '';
            localStorage.setItem(MODEL_SEARCH_STORAGE_KEY, chatState.modelSearch);
            renderModelList();
        });
    }

    els.form.addEventListener('submit', async (event) => {
        event.preventDefault();
        await sendChatMessage();
    });

    // Enter to send, Shift+Enter for newline
    els.textarea.addEventListener('keydown', async (event) => {
        if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
            event.preventDefault();
            await sendChatMessage();
        }
    });

    els.textarea.addEventListener('input', () => autoSizeComposer(els.textarea));

    els.stopBtn.addEventListener('click', () => {
        abortStreaming();
    });

    els.attachmentInput?.addEventListener('change', async (event) => {
        await addAttachments(event.target.files);
        event.target.value = '';
    });

    els.includeReasoningInput?.addEventListener('change', updateReasoningControls);
    els.includeManagementTools?.addEventListener('change', async () => {
        if (!chatState.sessionId) return;
        const previous = chatState.session?.includeManagementTools === true;
        try {
            await updateSession({
                include_management_tools: els.includeManagementTools.checked,
                refresh_tools: true,
            });
        } catch (error) {
            els.includeManagementTools.checked = previous;
            showChatAlert(getChatErrorMessage(error, 'Could not update management tools'), 'error');
        }
    });
    els.allServers?.addEventListener('change', async () => {
        const previous = chatState.serverAllowlist === null
            ? null
            : [...chatState.serverAllowlist];
        chatState.serverAllowlist = els.allServers.checked
            ? null
            : chatState.servers.filter((server) => server.enabled !== false).map((server) => server.name);
        chatState.serverSelectionExplicit = !els.allServers.checked;
        renderChatServerList();
        if (chatState.sessionId) {
            try {
                await updateSession({
                    server_allowlist: chatState.serverAllowlist,
                    refresh_tools: true,
                });
            } catch (error) {
                chatState.serverAllowlist = previous;
                renderChatServerList();
                showChatAlert(getChatErrorMessage(error, 'Could not update server access'), 'error');
            }
        }
    });
    els.refreshToolsBtn?.addEventListener('click', async () => {
        if (chatState.sessionId) {
            try {
                await updateSession({ refresh_tools: true });
                showChatAlert('Tool catalog refreshed', 'success');
            } catch (error) {
                showChatAlert(getChatErrorMessage(error, 'Could not refresh tools'), 'error');
            }
        }
    });

    document.addEventListener('click', (event) => {
        if (!chatState.modelPanelOpen) return;
        const picker = event.target.closest('.chat-model-picker');
        if (!picker) setModelPanelOpen(false, { restoreFocus: false });
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && chatState.modelPanelOpen) {
            event.preventDefault();
            setModelPanelOpen(false);
        }
    });
}

async function loadSkills() {
    try {
        chatState.skills = await fetchSkills();
    } catch (error) {
        console.warn('[CHAT] Failed to load skills', error);
        chatState.skills = [];
    }
}

function getSelectedSkillIds() {
    return (chatState.selectedSkillIds || []).filter(Boolean);
}

function renderSkillList() {
    const { skillListItems } = getChatElements();
    if (!skillListItems) return;
    skillListItems.innerHTML = '';
    const selected = new Set(getSelectedSkillIds());
    const enabledSkills = (chatState.skills || []).filter((item) => item.enabled !== false);
    if (!enabledSkills.length) {
        const empty = document.createElement('div');
        empty.className = 'empty-state';
        empty.textContent = 'No enabled skills';
        skillListItems.appendChild(empty);
        renderActiveSkills();
        return;
    }
    enabledSkills.forEach((skill) => {
        const row = document.createElement('label');
        row.className = 'chat-skill-option';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = selected.has(skill.id);
        const copy = document.createElement('span');
        copy.className = 'model-text';
        const title = document.createElement('strong');
        title.className = 'model-label';
        title.textContent = skill.title || skill.id;
        const id = document.createElement('span');
        id.className = 'model-sub';
        id.textContent = skill.id;
        copy.append(title, id);
        row.append(checkbox, copy);
        checkbox?.addEventListener('change', async () => {
            const next = new Set(getSelectedSkillIds());
            if (checkbox.checked) {
                next.add(skill.id);
            } else {
                next.delete(skill.id);
            }
            chatState.selectedSkillIds = Array.from(next);
            chatState.skillSelectionExplicit = true;
            renderActiveSkills();
            if (chatState.sessionId) {
                try {
                    await updateSession({ skill_ids: chatState.selectedSkillIds });
                } catch (error) {
                    checkbox.checked = !checkbox.checked;
                    chatState.selectedSkillIds = Array.from(selected);
                    renderActiveSkills();
                    showChatAlert(getChatErrorMessage(error, 'Could not update session skills'), 'error');
                }
            }
        });
        skillListItems.appendChild(row);
    });
    renderActiveSkills();
}

function renderActiveSkills() {
    const { activeSkills } = getChatElements();
    if (!activeSkills) return;
    const selected = getSelectedSkillIds();
    if (!chatState.skillSelectionExplicit) {
        activeSkills.textContent = 'All enabled skills (default)';
        return;
    }
    if (!selected.length) {
        activeSkills.textContent = 'No skills selected';
        return;
    }
    const names = selected.map((id) => {
        const skill = chatState.skills.find((item) => item.id === id);
        return skill?.title || id;
    });
    activeSkills.textContent = `Selected: ${names.join(', ')}`;
}

async function loadModels() {
    const { response, data } = await fetchJson('/chat/sessions/models');
    if (!response.ok || !Array.isArray(data?.models)) {
        chatState.models = [];
        throw new Error(getApiErrorMessage(response, data, 'Could not load chat models'));
    }
    chatState.models = data.models
        .filter((model) => model && model.id && model.provider)
        .map((model) => ({
            ...model,
            id: String(model.id),
            provider: String(model.provider),
            key: model.key || `${model.provider}:${model.id}`,
            label: model.label || model.id,
            providerLabel: model.providerLabel || model.provider,
            billing: model.billing || 'unknown',
            free: model.free === true,
            capabilities: Array.isArray(model.capabilities) ? model.capabilities : [],
            inputModalities: Array.isArray(model.inputModalities) ? model.inputModalities : ['text'],
        }));
}

async function loadFavorites() {
    const readLocalFavorites = () => {
        try {
            const parsed = JSON.parse(localStorage.getItem(FAVORITES_STORAGE_KEY) || '[]');
            return Array.isArray(parsed) ? parsed.map(String) : [];
        } catch {
            return [];
        }
    };
    try {
        const { response, data } = await fetchJson('/chat/sessions/favorites');
        if (response.ok && Array.isArray(data.favorites)) {
            chatState.favorites = data.favorites.map(String);
            localStorage.setItem(FAVORITES_STORAGE_KEY, JSON.stringify(chatState.favorites));
        } else {
            chatState.favorites = readLocalFavorites();
        }
    } catch (error) {
        console.warn('[CHAT] Failed to load favorites from server, using localStorage', error);
        chatState.favorites = readLocalFavorites();
    }

    const savedSearch = localStorage.getItem(MODEL_SEARCH_STORAGE_KEY);
    if (typeof savedSearch === 'string') {
        chatState.modelSearch = savedSearch;
        const els = getChatElements();
        if (els.modelSearchInput) {
            els.modelSearchInput.value = savedSearch;
        }
    }
    const savedProviderFilter = localStorage.getItem(PROVIDER_FILTER_STORAGE_KEY);
    if (typeof savedProviderFilter === 'string' && savedProviderFilter) {
        chatState.providerFilter = savedProviderFilter;
    }
}

async function saveFavorites() {
    localStorage.setItem(FAVORITES_STORAGE_KEY, JSON.stringify(chatState.favorites || []));
    const { response, data } = await fetchJson('/chat/sessions/favorites', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ models: chatState.favorites || [] }),
    });
    if (!response.ok || data?.ok !== true) {
        throw new Error(getApiErrorMessage(response, data, 'Could not save favorite models'));
    }
}

function normalizeFavoriteKeys() {
    const normalized = new Set();
    (chatState.favorites || []).forEach((favorite) => {
        const exact = chatState.models.find((model) => providerQualifiedValue(model) === favorite);
        if (exact) {
            normalized.add(providerQualifiedValue(exact));
            return;
        }
        const matches = chatState.models.filter((model) => model.id === favorite);
        normalized.add(matches.length === 1 ? providerQualifiedValue(matches[0]) : favorite);
    });
    chatState.favorites = Array.from(normalized);
    localStorage.setItem(FAVORITES_STORAGE_KEY, JSON.stringify(chatState.favorites));
}

async function toggleFavorite(modelKey) {
    if (!modelKey) return;
    const previous = [...chatState.favorites];
    const set = new Set(chatState.favorites || []);
    if (set.has(modelKey)) {
        set.delete(modelKey);
    } else {
        set.add(modelKey);
    }
    chatState.favorites = Array.from(set);
    populateModelSelect();
    renderModelList();
    try {
        await saveFavorites();
    } catch (error) {
        chatState.favorites = previous;
        populateModelSelect();
        renderModelList();
        showChatAlert(getChatErrorMessage(error, 'Could not save favorites'), 'error');
    }
}

function getAllowedModels() {
    return chatState.models;
}

function ensureAllowedModel() {
    const allowed = getAllowedModels();
    if (!allowed.length) {
        chatState.selectedModelKey = '';
        return null;
    }
    const sessionMatch = allowed.find((model) => (
        model.id === chatState.session?.model
        && model.provider === chatState.session?.provider
    ));
    const selected = allowed.find((model) => providerQualifiedValue(model) === chatState.selectedModelKey);
    const favorites = new Set(chatState.favorites || []);
    const next = sessionMatch || selected || allowed.find((model) => favorites.has(providerQualifiedValue(model))) || allowed[0];
    chatState.selectedModelKey = providerQualifiedValue(next);
    return next;
}

function populateModelSelect() {
    const {
        modelSelect,
        selectedModelLabel,
        selectedModelProvider,
        modelCapabilities,
    } = getChatElements();
    if (!modelSelect) return;
    const selected = ensureAllowedModel();
    modelSelect.value = selected ? providerQualifiedValue(selected) : '';
    if (selectedModelLabel) selectedModelLabel.textContent = selected?.label || 'Choose a model';
    if (selectedModelProvider) {
        selectedModelProvider.textContent = selected
            ? `${selected.providerLabel} · ${selected.id}`
            : 'No provider models available';
    }
    if (modelCapabilities) {
        const capabilities = selected?.capabilities || [];
        const modalities = selected?.inputModalities || [];
        const parts = [...new Set([...capabilities, ...modalities])];
        modelCapabilities.textContent = parts.length
            ? `Capabilities: ${parts.join(', ')}`
            : 'No capability metadata published';
    }
}

function getProviderCatalog() {
    const providers = new Map();
    chatState.models.forEach((model) => {
        const current = providers.get(model.provider) || {
            id: model.provider,
            label: model.providerLabel || model.provider,
            count: 0,
        };
        current.count += 1;
        providers.set(model.provider, current);
    });
    return Array.from(providers.values()).sort((left, right) => (
        left.label.localeCompare(right.label)
    ));
}

function renderProviderFilter() {
    const { providerFilterSelect } = getChatElements();
    if (!providerFilterSelect) return;
    const providers = getProviderCatalog();
    const available = new Set(providers.map((provider) => provider.id));
    if (chatState.providerFilter !== 'all' && !available.has(chatState.providerFilter)) {
        chatState.providerFilter = 'all';
        localStorage.setItem(PROVIDER_FILTER_STORAGE_KEY, chatState.providerFilter);
    }

    providerFilterSelect.innerHTML = '';
    const allOption = document.createElement('option');
    allOption.value = 'all';
    allOption.textContent = `All providers (${chatState.models.length})`;
    providerFilterSelect.appendChild(allOption);
    providers.forEach((provider) => {
        const option = document.createElement('option');
        option.value = provider.id;
        option.textContent = `${provider.label} (${provider.count})`;
        providerFilterSelect.appendChild(option);
    });
    providerFilterSelect.value = chatState.providerFilter;
    providerFilterSelect.disabled = providers.length === 0;
}

function setBrowsingProviderFilter(providerId, { persist = true } = {}) {
    const available = new Set(chatState.models.map((model) => model.provider));
    chatState.providerFilter = providerId === 'all' || available.has(providerId)
        ? providerId
        : 'all';
    if (persist) {
        localStorage.setItem(PROVIDER_FILTER_STORAGE_KEY, chatState.providerFilter);
    }
    renderProviderFilter();
    renderModelList();
}

function handleProviderFilterChange(event) {
    setBrowsingProviderFilter(event?.target?.value || 'all');
    setModelPanelOpen(true);
}

function syncProviderFilterToSelectedModel(model = null) {
    const selected = model || chatState.models.find(
        (candidate) => providerQualifiedValue(candidate) === chatState.selectedModelKey,
    );
    if (!selected) {
        renderProviderFilter();
        return;
    }
    setBrowsingProviderFilter(selected.provider);
}

function renderModelList() {
    const {
        modelList,
        modelListItems,
        modelSearchInput,
        modelListStatus,
        modelFilterButtons,
    } = getChatElements();
    if (!modelList || !modelListItems) return;
    if (!chatState.modelPanelOpen) {
        modelList.classList.add('hidden');
        return;
    }
    modelList.classList.remove('hidden');
    modelListItems.innerHTML = '';

    const favorites = new Set(chatState.favorites || []);
    const query = (chatState.modelSearch || '').trim().toLowerCase();

    const models = chatState.models.filter((model) => {
        if (
            chatState.providerFilter !== 'all'
            && model.provider !== chatState.providerFilter
        ) return false;
        const key = providerQualifiedValue(model);
        if (chatState.modelFilter === 'favorites' && !favorites.has(key)) return false;
        if (chatState.modelFilter === 'free' && model.free !== true) return false;
        if (!query) return true;
        const haystack = [
            model.id,
            model.label,
            model.provider,
            model.providerLabel,
            ...(model.capabilities || []),
            ...(model.inputModalities || []),
        ].join(' ').toLowerCase();
        return haystack.includes(query);
    });

    const groups = new Map();
    models.forEach((model) => {
        const group = groups.get(model.provider) || {
            label: model.providerLabel || model.provider,
            models: [],
        };
        group.models.push(model);
        groups.set(model.provider, group);
    });

    groups.forEach((group, providerId) => {
        const section = document.createElement('section');
        section.className = 'model-provider-group';
        section.dataset.provider = providerId;
        const heading = document.createElement('div');
        heading.className = 'model-provider-heading';
        const providerName = document.createElement('span');
        providerName.textContent = group.label;
        const count = document.createElement('span');
        count.textContent = String(group.models.length);
        heading.append(providerName, count);
        section.appendChild(heading);

        group.models.forEach((model) => {
            const key = providerQualifiedValue(model);
            const row = document.createElement('div');
            row.className = 'model-row';
            row.dataset.modelKey = key;

            const choice = document.createElement('button');
            choice.type = 'button';
            choice.className = 'model-choice';
            if (key === chatState.selectedModelKey) {
                choice.setAttribute('aria-current', 'true');
            }
            const label = document.createElement('strong');
            label.className = 'model-label';
            label.textContent = model.label || model.id;
            const sub = document.createElement('span');
            sub.className = 'model-sub';
            sub.textContent = model.id;
            const meta = document.createElement('span');
            meta.className = 'model-meta';
            modelBadges(model).forEach((badge) => {
                const node = document.createElement('span');
                node.className = `model-badge ${badge.className}`.trim();
                node.textContent = badge.label;
                meta.appendChild(node);
            });
            choice.append(label, sub, meta);
            choice.addEventListener('click', () => selectModel(model));

            const star = document.createElement('button');
            star.type = 'button';
            star.className = `model-star ${favorites.has(key) ? 'active' : ''}`;
            star.setAttribute('aria-pressed', String(favorites.has(key)));
            star.setAttribute('aria-label', favorites.has(key) ? 'Remove favorite' : 'Add favorite');
            star.title = favorites.has(key) ? 'Remove favorite' : 'Add favorite';
            star.textContent = '★';
            star.addEventListener('click', () => toggleFavorite(key));

            row.append(choice, star);
            section.appendChild(row);
        });
        modelListItems.appendChild(section);
    });

    if (!models.length) {
        const empty = document.createElement('div');
        empty.className = 'empty-state';
        empty.textContent = chatState.models.length
            ? 'No models match this search and filter.'
            : 'No provider models are configured.';
        modelListItems.appendChild(empty);
    }

    if (modelSearchInput && modelSearchInput.value !== chatState.modelSearch) {
        modelSearchInput.value = chatState.modelSearch;
    }
    modelFilterButtons.forEach((button) => {
        const active = button.dataset.modelFilter === chatState.modelFilter;
        button.classList.toggle('active', active);
        button.setAttribute('aria-pressed', String(active));
    });
    if (modelListStatus) {
        const providerTotal = chatState.providerFilter === 'all'
            ? chatState.models.length
            : chatState.models.filter(
                (model) => model.provider === chatState.providerFilter,
            ).length;
        modelListStatus.textContent = `${models.length} of ${providerTotal} models in this provider view. Free includes zero-price, rate-limited, and local models.`;
    }
}

function providerQualifiedValue(model) {
    return model.key || `${model.provider}:${model.id}`;
}

function modelBadges(model) {
    const badges = [];
    const billingBadges = {
        free: { label: 'Free model', className: 'free-zero' },
        free_rate_limited: { label: 'Free tier', className: 'free-tier' },
        local: { label: 'Local', className: 'local' },
        trial_credit: { label: 'Trial credit', className: '' },
    };
    if (billingBadges[model.billing]) badges.push(billingBadges[model.billing]);
    if (model.contextWindow) badges.push({ label: `${Number(model.contextWindow).toLocaleString()} ctx`, className: '' });
    (model.capabilities || []).slice(0, 3).forEach((capability) => {
        badges.push({ label: String(capability), className: '' });
    });
    return badges;
}

function setModelPanelOpen(open, { restoreFocus = true } = {}) {
    const wasOpen = chatState.modelPanelOpen;
    chatState.modelPanelOpen = Boolean(open);
    const { modelTrigger, modelSearchInput } = getChatElements();
    modelTrigger?.setAttribute('aria-expanded', String(chatState.modelPanelOpen));
    renderModelList();
    if (chatState.modelPanelOpen) {
        requestAnimationFrame(() => modelSearchInput?.focus());
    } else if (wasOpen && restoreFocus) {
        requestAnimationFrame(() => modelTrigger?.focus());
    }
}

async function selectModel(model) {
    const previousKey = chatState.selectedModelKey;
    const previousProviderFilter = chatState.providerFilter;
    chatState.selectedModelKey = providerQualifiedValue(model);
    syncProviderFilterToSelectedModel(model);
    populateModelSelect();
    renderModelList();
    setModelPanelOpen(false);
    if (!chatState.sessionId) return;
    try {
        await updateSession({ provider: model.provider, model: model.id });
    } catch (error) {
        chatState.selectedModelKey = previousKey;
        chatState.providerFilter = previousProviderFilter;
        localStorage.setItem(PROVIDER_FILTER_STORAGE_KEY, chatState.providerFilter);
        renderProviderFilter();
        populateModelSelect();
        renderModelList();
        showChatAlert(getChatErrorMessage(error, 'Could not change model'), 'error');
    }
}

async function loadChatSettings() {
    const { response, data } = await fetchJson('/chat/sessions/settings');
    if (!response.ok || !data?.settings) {
        showChatAlert(getApiErrorMessage(response, data, 'Could not load chat defaults'), 'error');
        return;
    }
    chatState.chatSettings = { ...chatState.chatSettings, ...data.settings };
}

function applyAgentSettingsToControls() {
    const els = getChatElements();
    const settings = chatState.chatSettings;
    if (els.temperatureInput) {
        els.temperatureInput.value = settings.temperature ?? '';
    }
    if (els.maxOutputInput) {
        els.maxOutputInput.value = settings.maxOutputTokens ?? '';
    }
    if (els.maxToolRoundsInput) {
        els.maxToolRoundsInput.value = settings.maxToolRounds ?? 8;
    }
    if (els.includeReasoningInput) {
        els.includeReasoningInput.checked = settings.includeReasoning !== false;
    }
    if (els.reasoningEffortSelect) {
        els.reasoningEffortSelect.value = settings.reasoningEffort || '';
    }
    if (els.includeManagementTools) {
        els.includeManagementTools.checked = chatState.session
            ? chatState.session.includeManagementTools === true
            : settings.includeManagementTools === true;
    }
    updateReasoningControls();
}

function updateReasoningControls() {
    const { includeReasoningInput, reasoningEffortSelect } = getChatElements();
    if (reasoningEffortSelect) {
        reasoningEffortSelect.disabled = includeReasoningInput?.checked === false;
    }
}

async function loadChatServers() {
    chatState.serverCatalogLoaded = false;
    chatState.serverCatalogError = '';
    const { response, data } = await fetchJson('/_meta/servers');
    if (!response.ok || data?.ok !== true || !Array.isArray(data?.servers)) {
        chatState.servers = [];
        chatState.serverAllowlist = [];
        chatState.serverCatalogError = getApiErrorMessage(
            response,
            data,
            'Server catalog could not be loaded',
        );
        showChatAlert(
            `${chatState.serverCatalogError}. New sessions will start with no MCP servers.`,
            'error',
        );
        renderChatServerList();
        return false;
    }
    chatState.serverCatalogLoaded = true;
    chatState.servers = data.servers
        .filter((server) => server && server.name).map((server) => ({
            ...server,
            name: String(server.name),
        }));
    if (!chatState.session && !chatState.serverSelectionExplicit) {
        chatState.serverAllowlist = null;
    }
    return true;
}

function renderChatServerList() {
    const { serverList, allServers, toolCount } = getChatElements();
    if (toolCount) {
        toolCount.textContent = String(chatState.session?.tools?.length || 0);
    }
    if (!serverList) return;
    serverList.innerHTML = '';
    const enabledServers = chatState.servers.filter((server) => server.enabled !== false);
    if (allServers) {
        allServers.checked = chatState.serverCatalogLoaded && chatState.serverAllowlist === null;
        allServers.disabled = !chatState.serverCatalogLoaded;
    }
    if (!chatState.serverCatalogLoaded) {
        const unavailable = document.createElement('div');
        unavailable.className = 'empty-state';
        unavailable.textContent = 'Server catalog unavailable. Tool access is locked to none.';
        serverList.appendChild(unavailable);
        return;
    }
    if (!enabledServers.length) {
        const empty = document.createElement('div');
        empty.className = 'empty-state';
        empty.textContent = 'No enabled MCP servers';
        serverList.appendChild(empty);
        return;
    }
    const allowed = new Set(chatState.serverAllowlist || []);
    enabledServers.forEach((server) => {
        const row = document.createElement('label');
        row.className = 'chat-server-option';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = chatState.serverAllowlist === null || allowed.has(server.name);
        const name = document.createElement('span');
        name.textContent = server.name;
        row.append(checkbox, name);
        checkbox.addEventListener('change', async () => {
            const previous = chatState.serverAllowlist === null
                ? null
                : [...chatState.serverAllowlist];
            const next = new Set(
                chatState.serverAllowlist === null
                    ? enabledServers.map((item) => item.name)
                    : chatState.serverAllowlist,
            );
            if (checkbox.checked) next.add(server.name);
            else next.delete(server.name);
            chatState.serverSelectionExplicit = true;
            chatState.serverAllowlist = Array.from(next);
            renderChatServerList();
            if (!chatState.sessionId) return;
            try {
                await updateSession({
                    server_allowlist: chatState.serverAllowlist,
                    refresh_tools: true,
                });
            } catch (error) {
                chatState.serverAllowlist = previous;
                renderChatServerList();
                showChatAlert(getChatErrorMessage(error, 'Could not update tool access'), 'error');
            }
        });
        serverList.appendChild(row);
    });
}

function getSelectedModel() {
    return chatState.models.find(
        (model) => providerQualifiedValue(model) === chatState.selectedModelKey,
    ) || null;
}

function inferAttachmentType(file) {
    const extension = (file.name.match(/\.[^.]+$/)?.[0] || '').toLowerCase();
    const fallbackMime = {
        '.md': 'text/markdown',
        '.txt': 'text/plain',
        '.csv': 'text/csv',
        '.log': 'text/plain',
        '.py': 'text/x-python',
        '.js': 'application/javascript',
        '.ts': 'text/typescript',
        '.yaml': 'application/yaml',
        '.yml': 'application/yaml',
        '.json': 'application/json',
        '.xml': 'application/xml',
        '.pdf': 'application/pdf',
    }[extension];
    const declaredMime = (file.type || '').toLowerCase();
    const declaredSupported = IMAGE_MIME_TYPES.has(declaredMime)
        || declaredMime === 'application/pdf'
        || declaredMime.startsWith('text/')
        || TEXT_MIME_TYPES.has(declaredMime);
    const mimeType = declaredSupported ? declaredMime : (fallbackMime || declaredMime);
    if (IMAGE_MIME_TYPES.has(mimeType)) return { type: 'image', mimeType };
    if (mimeType === 'application/pdf') return { type: 'file', mimeType };
    if (mimeType.startsWith('text/') || TEXT_MIME_TYPES.has(mimeType)) {
        return { type: 'text', mimeType };
    }
    return null;
}

function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    const chunkSize = 0x8000;
    for (let offset = 0; offset < bytes.length; offset += chunkSize) {
        binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
    }
    return btoa(binary);
}

async function addAttachments(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    const errors = [];
    let totalBytes = chatState.attachments.reduce((sum, attachment) => sum + attachment.sizeBytes, 0);
    for (const file of files) {
        if (chatState.attachments.length >= MAX_CHAT_ATTACHMENTS) {
            errors.push(`Only ${MAX_CHAT_ATTACHMENTS} attachments are allowed.`);
            break;
        }
        const classification = inferAttachmentType(file);
        if (!classification) {
            errors.push(`${file.name}: unsupported file type.`);
            continue;
        }
        if (file.size > MAX_CHAT_ATTACHMENT_BYTES) {
            errors.push(`${file.name}: exceeds 5 MiB.`);
            continue;
        }
        if (totalBytes + file.size > MAX_CHAT_ATTACHMENT_TOTAL_BYTES) {
            errors.push(`${file.name}: attachments would exceed 20 MiB total.`);
            continue;
        }
        try {
            const buffer = await file.arrayBuffer();
            if (classification.type === 'text') {
                new TextDecoder('utf-8', { fatal: true }).decode(buffer);
            }
            const id = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
            chatState.attachments.push({
                id,
                type: classification.type,
                name: file.name,
                mimeType: classification.mimeType,
                sizeBytes: file.size,
                data: arrayBufferToBase64(buffer),
                previewUrl: classification.type === 'image' ? URL.createObjectURL(file) : '',
            });
            totalBytes += file.size;
        } catch (error) {
            errors.push(`${file.name}: ${classification.type === 'text' ? 'not valid UTF-8' : 'could not be read'}.`);
        }
    }
    renderAttachmentPreview();
    if (errors.length) showChatAlert(errors.join(' '), 'error');
}

function removeAttachment(attachmentId) {
    const index = chatState.attachments.findIndex((attachment) => attachment.id === attachmentId);
    if (index < 0) return;
    const [removed] = chatState.attachments.splice(index, 1);
    if (removed.previewUrl) URL.revokeObjectURL(removed.previewUrl);
    renderAttachmentPreview();
}

function clearAttachments() {
    chatState.attachments.forEach((attachment) => {
        if (attachment.previewUrl) URL.revokeObjectURL(attachment.previewUrl);
    });
    chatState.attachments = [];
    renderAttachmentPreview();
}

function renderAttachmentPreview() {
    const { attachmentPreview } = getChatElements();
    if (!attachmentPreview) return;
    attachmentPreview.innerHTML = '';
    chatState.attachments.forEach((attachment) => {
        const chip = document.createElement('div');
        chip.className = 'chat-attachment-chip';
        if (attachment.previewUrl) {
            const image = document.createElement('img');
            image.className = 'chat-attachment-thumb';
            image.src = attachment.previewUrl;
            image.alt = '';
            chip.appendChild(image);
        } else {
            const icon = document.createElement('span');
            icon.className = 'chat-attachment-icon';
            icon.textContent = attachment.type === 'file' ? 'PDF' : 'TXT';
            chip.appendChild(icon);
        }
        const copy = document.createElement('span');
        copy.className = 'chat-attachment-copy';
        const name = document.createElement('strong');
        name.textContent = attachment.name;
        const size = document.createElement('span');
        size.textContent = formatChatBytes(attachment.sizeBytes);
        copy.append(name, size);
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'chat-attachment-remove';
        remove.setAttribute('aria-label', `Remove ${attachment.name}`);
        remove.textContent = '×';
        remove.addEventListener('click', () => removeAttachment(attachment.id));
        chip.append(copy, remove);
        attachmentPreview.appendChild(chip);
    });
}

function formatChatBytes(size) {
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KiB`;
    return `${(size / (1024 * 1024)).toFixed(1)} MiB`;
}

function validateAttachmentSupport() {
    const model = getSelectedModel();
    if (!model || !chatState.attachments.length) return;
    const modalities = new Set((model.inputModalities || ['text']).map((item) => String(item).toLowerCase()));
    if (chatState.attachments.some((attachment) => attachment.type === 'image') && !modalities.has('image')) {
        throw new Error(`${model.label} does not advertise image input support.`);
    }
    if (chatState.attachments.some((attachment) => attachment.type === 'file') && !modalities.has('file')) {
        throw new Error(`${model.label} does not advertise PDF/file input support.`);
    }
}

function autoSizeComposer(textarea) {
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(Math.max(textarea.scrollHeight, 44), 180)}px`;
}

function optionalNumber(input, integer = false) {
    const raw = input?.value?.trim();
    if (!raw) return null;
    const value = integer ? Number.parseInt(raw, 10) : Number.parseFloat(raw);
    return Number.isFinite(value) ? value : null;
}

async function authenticatedFetch(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const apiKey = localStorage.getItem('mcpo-api-key');
    if (apiKey && !headers.has('Authorization')) {
        headers.set('Authorization', `Bearer ${apiKey}`);
    }
    return fetch(path, { ...options, headers });
}

async function responseErrorMessage(response, fallback) {
    try {
        const data = await response.json();
        return getApiErrorMessage(response, data, fallback);
    } catch {
        return response.status ? `${fallback} (HTTP ${response.status})` : fallback;
    }
}

function getChatErrorMessage(error, fallback = 'Request failed') {
    if (typeof error === 'string' && error) return error;
    if (error && typeof error.message === 'string' && error.message) return error.message;
    if (error && typeof error.detail === 'string' && error.detail) return error.detail;
    return fallback;
}

async function ensureSession() {
    const storedId = localStorage.getItem(CHAT_SESSION_STORAGE_KEY);
    if (storedId) {
        const session = await fetchSession(storedId);
        if (session) {
            chatState.sessionId = storedId;
            chatState.session = session;
            chatState.selectedSkillIds = Array.isArray(session.skillIds) ? session.skillIds.slice() : [];
            if (chatState.serverCatalogLoaded) {
                chatState.serverAllowlist = Array.isArray(session.serverAllowlist)
                    ? session.serverAllowlist.slice()
                    : null;
                chatState.serverSelectionExplicit = Array.isArray(session.serverAllowlist);
            } else {
                chatState.serverAllowlist = [];
                chatState.serverSelectionExplicit = true;
            }
            chatState.selectedModelKey = session.provider && session.model
                ? `${session.provider}:${session.model}`
                : chatState.selectedModelKey;
            if (!chatState.serverCatalogLoaded) {
                try {
                    await updateSession({ server_allowlist: [], refresh_tools: true });
                } catch (error) {
                    chatState.sessionId = null;
                    chatState.session = null;
                    showChatAlert(
                        getChatErrorMessage(
                            error,
                            'Existing session tool access could not be locked while the server catalog is unavailable',
                        ),
                        'error',
                    );
                }
                return;
            }
            populateModelSelect();
            renderSkillList();
            renderChatServerList();
            return;
        }
        localStorage.removeItem(CHAT_SESSION_STORAGE_KEY);
    }
    await createSession();
}

async function createSession() {
    const selectedModel = ensureAllowedModel();
    if (!selectedModel) {
        showChatAlert('No models are available. Configure and test a provider in Settings.', 'error');
        return;
    }
    try {
        const payload = {
            provider: selectedModel.provider,
            model: selectedModel.id,
            system_prompt: chatState.chatSettings.defaultSystemPrompt || null,
        };
        payload.server_allowlist = chatState.serverCatalogLoaded
            ? chatState.serverAllowlist
            : [];
        payload.include_management_tools = Boolean(
            getChatElements().includeManagementTools?.checked,
        );
        if (chatState.skillSelectionExplicit) {
            payload.skill_ids = getSelectedSkillIds();
        }
        const { response, data } = await fetchJson('/chat/sessions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!response.ok || !data?.session) {
            throw new Error(getApiErrorMessage(response, data, 'Failed to create session'));
        }
        chatState.sessionId = data.session.id;
        chatState.session = data.session;
        chatState.selectedSkillIds = Array.isArray(data.session.skillIds) ? data.session.skillIds.slice() : [];
        chatState.serverAllowlist = Array.isArray(data.session.serverAllowlist)
            ? data.session.serverAllowlist.slice()
            : chatState.serverAllowlist;
        chatState.selectedModelKey = `${data.session.provider}:${data.session.model}`;
        chatState.stepMessages = {};
        chatState.currentStepId = null;
        localStorage.setItem(CHAT_SESSION_STORAGE_KEY, chatState.sessionId);
        populateModelSelect();
        renderSkillList();
        renderChatServerList();
        renderSession();
    } catch (error) {
        console.error('[CHAT] createSession failed', error);
        chatState.sessionId = null;
        chatState.session = null;
        showChatAlert(getChatErrorMessage(error, 'Session creation failed'), 'error');
    }
}

async function fetchSession(sessionId) {
    try {
        const { response, data } = await fetchJson(`/chat/sessions/${sessionId}`);
        if (response.ok && data.session) {
            return data.session;
        }
    } catch (error) {
        console.warn('[CHAT] fetchSession failed', error);
    }
    return null;
}

async function resetSession() {
    if (!chatState.sessionId) return;
    try {
        const { response, data } = await fetchJson(`/chat/sessions/${chatState.sessionId}/reset`, {
            method: 'POST',
        });
        if (!response.ok || !data?.session) {
            throw new Error(getApiErrorMessage(response, data, 'Failed to reset session'));
        }
        chatState.session = data.session;
        chatState.selectedSkillIds = Array.isArray(data.session.skillIds) ? data.session.skillIds.slice() : [];
        chatState.stepMessages = {};
        chatState.currentStepId = null;
        await updateSession({ refresh_tools: true });
        renderSession();
        showChatAlert('Session cleared', 'info');
    } catch (error) {
        console.error('[CHAT] resetSession failed', error);
        showChatAlert(getChatErrorMessage(error, 'Failed to reset session'), 'error');
    }
}

async function startNewSession() {
    abortStreaming();
    chatState.sessionId = null;
    chatState.session = null;
    chatState.stepMessages = {};
    chatState.currentStepId = null;
    chatState.expandedReasoningIds.clear();
    chatState.expandedToolIds.clear();
    localStorage.removeItem(CHAT_SESSION_STORAGE_KEY);
    renderSession();
    await createSession();
}

async function updateSession(payload) {
    if (!chatState.sessionId) return null;
    const { response, data } = await fetchJson(`/chat/sessions/${chatState.sessionId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    });
    if (!response.ok || !data?.session) {
        throw new Error(getApiErrorMessage(response, data, 'Could not update chat session'));
    }
    chatState.session = data.session;
    chatState.selectedSkillIds = Array.isArray(data.session.skillIds)
        ? data.session.skillIds.slice()
        : chatState.selectedSkillIds;
    if (Object.prototype.hasOwnProperty.call(payload, 'server_allowlist')) {
        chatState.serverAllowlist = Array.isArray(payload.server_allowlist)
            ? payload.server_allowlist.slice()
            : null;
    } else if (Array.isArray(data.session.serverAllowlist)) {
        chatState.serverAllowlist = data.session.serverAllowlist.slice();
    }
    if (data.session.provider && data.session.model) {
        chatState.selectedModelKey = `${data.session.provider}:${data.session.model}`;
    }
    populateModelSelect();
    renderSkillList();
    renderChatServerList();
    renderSession();
    return data.session;
}

async function persistSessionMeta() {
    populateModelSelect();
    renderSkillList();
    renderChatServerList();
    renderSession();
}

async function sendChatMessage() {
    const els = getChatElements();
    if (!els.textarea || chatState.streaming) return;

    const message = (els.textarea.value || '').trim();
    if (!message && !chatState.attachments.length) {
        return;
    }

    await ensureSession();
    if (!chatState.sessionId) {
        showChatAlert('Cannot send message: no session', 'error');
        return;
    }

    try {
        validateAttachmentSupport();
    } catch (error) {
        showChatAlert(getChatErrorMessage(error), 'error');
        return;
    }

    const stream = els.streamToggle?.checked !== false;
    const selectedModel = getSelectedModel();
    if (!selectedModel) {
        showChatAlert('Choose an available model before sending.', 'error');
        return;
    }
    const attachmentMetadata = chatState.attachments.map((attachment) => ({
        type: attachment.type,
        name: attachment.name,
        mimeType: attachment.mimeType,
        sizeBytes: attachment.sizeBytes,
    }));

    appendUserMessage(message, attachmentMetadata);
    els.textarea.value = '';
    autoSizeComposer(els.textarea);

    const payload = {
        message,
        stream,
        provider: selectedModel.provider,
        model: selectedModel.id,
        temperature: optionalNumber(els.temperatureInput),
        max_output_tokens: optionalNumber(els.maxOutputInput, true),
        max_tool_rounds: optionalNumber(els.maxToolRoundsInput, true) || 8,
        include_reasoning: els.includeReasoningInput?.checked !== false,
        reasoning_effort: els.includeReasoningInput?.checked === false
            ? null
            : (els.reasoningEffortSelect?.value || null),
        attachments: chatState.attachments.map((attachment) => ({
            type: attachment.type,
            name: attachment.name,
            mime_type: attachment.mimeType,
            data: attachment.data,
        })),
    };
    if (chatState.skillSelectionExplicit) payload.skill_ids = getSelectedSkillIds();

    if (stream) {
        await sendStreamingMessage(payload);
    } else {
        await sendStandardMessage(payload);
    }
}

async function sendStandardMessage(payload) {
    try {
        const { response, data } = await fetchJson(`/chat/sessions/${chatState.sessionId}/messages`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!response.ok || !data?.session) {
            throw new Error(getApiErrorMessage(response, data, 'Chat request failed'));
        }
        clearAttachments();
        chatState.session = data.session;
        chatState.selectedSkillIds = Array.isArray(data.session.skillIds) ? data.session.skillIds.slice() : [];
        chatState.stepMessages = {};
        chatState.currentStepId = null;
        renderSession();
    } catch (error) {
        console.error('[CHAT] sendStandardMessage failed', error);
        showChatAlert(`Chat failed: ${getChatErrorMessage(error)}`, 'error');
    }
}

async function sendStreamingMessage(payload) {
    abortStreaming();

    chatState.streaming = true;
    const els = getChatElements();
    if (els.stopBtn) els.stopBtn.disabled = false;
    toggleFormDisabled(true);

    const controller = new AbortController();
    chatState.abortController = controller;
    chatState.buffer = '';

    try {
        const response = await authenticatedFetch(`/chat/sessions/${chatState.sessionId}/messages`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            signal: controller.signal,
        });

        if (!response.ok) {
            throw new Error(await responseErrorMessage(response, 'Streaming response failed'));
        }
        if (!response.body) {
            throw new Error('Streaming response did not include a readable body');
        }
        clearAttachments();

        const reader = response.body.getReader();
        const decoder = new TextDecoder();

        while (chatState.streaming) {
            const { value, done } = await reader.read();
            if (done) break;
            if (!value) continue;
            const chunk = decoder.decode(value, { stream: true });
            chatState.buffer += chunk;
            await processSSEBuffer();
        }
    } catch (error) {
        if (controller.signal.aborted) {
            showChatAlert('Streaming aborted', 'info');
        } else {
            console.error('[CHAT] streaming error', error);
            showChatAlert(`Streaming error: ${getChatErrorMessage(error)}`, 'error');
        }
    } finally {
        finalizeStreaming();
        await refreshSessionState();
    }
}

async function processSSEBuffer() {
    const messages = chatState.buffer.split('\n\n');
    chatState.buffer = messages.pop();

    for (const raw of messages) {
        if (!raw.trim()) continue;
        const line = raw.trim();
        if (!line.startsWith('data:')) continue;
        const payload = line.slice('data:'.length).trim();
        if (!payload || payload === '[DONE]') continue;
        try {
            const event = JSON.parse(payload);
            await handleStreamEvent(event);
        } catch (error) {
            console.warn('[CHAT] Failed to parse stream event', payload, error);
        }
    }
}

async function handleStreamEvent(event) {
    switch (event.type) {
        case 'session.updated':
            chatState.session = event.session;
            if (event.session?.provider && event.session?.model) {
                chatState.selectedModelKey = `${event.session.provider}:${event.session.model}`;
            }
            renderSession();
            break;
        case 'skills.loaded':
            displaySkillsLoaded(event.skills || []);
            break;
        case 'step.started':
            appendStepMessage(event.step);
            break;
        case 'step.completed':
            updateStepMessage(event.step, event.status);
            break;
        case 'tool.call.started':
            appendToolCall(event.toolCall);
            break;
        case 'tool.call.delta':
            updateToolCallDelta(event.toolCall);
            break;
        case 'tool.call.result':
            completeToolCall(event.toolCall);
            break;
        case 'message.delta':
            appendStreamingMessageDelta(event.text);
            break;
        case 'reasoning.delta':
            appendStreamingReasoningDelta(event.text);
            break;
        case 'message.completed':
            finalizeAssistantMessage(event.message);
            break;
        case 'error':
            showChatAlert(event.message || 'Streaming error', 'error');
            break;
        case 'done':
            finalizeStreaming();
            break;
        default:
            break;
    }
}

function displaySkillsLoaded(skills) {
    if (!skills.length) return;
    const container = document.getElementById('chat-messages');
    if (!container) return;
    const names = skills.map((s) => s.title || s.id).join(', ');
    const el = document.createElement('div');
    el.className = 'skills-loaded-indicator';
    el.innerHTML = `<span class="skills-loaded-icon">&#9889;</span> Skills loaded: <strong>${escapeHtml(names)}</strong>`;
    container.appendChild(el);
    el.scrollIntoView({ behavior: 'smooth', block: 'end' });
}

function appendUserMessage(content, attachments = []) {
    chatState.session = chatState.session || { messages: [] };
    chatState.session.messages = chatState.session.messages || [];
    chatState.session.messages.push({ role: 'user', content, attachments });
    renderMessages();
}

function appendStreamingMessageDelta(text) {
    if (!text) return;
    const messages = chatState.session.messages || [];
    let current = messages[messages.length - 1];

    // Create new message if current is a step, has tools, or is finished
    if (!current || current.role !== 'assistant' || current.streamingFinished || current.step || (current.tool_calls && current.tool_calls.length > 0)) {
        current = { role: 'assistant', content: '', reasoning: '' };
        messages.push(current);
    }
    current.content = (current.content || '') + text;
    renderMessages();
}

function appendStreamingReasoningDelta(text) {
    if (!text) return;
    const messages = chatState.session.messages || [];
    let current = messages[messages.length - 1];

    // Create new message if current is a step, has tools, or is finished
    if (!current || current.role !== 'assistant' || current.streamingFinished || current.step || (current.tool_calls && current.tool_calls.length > 0)) {
        current = { role: 'assistant', content: '', reasoning: '' };
        messages.push(current);
    }
    current.reasoning = (current.reasoning || '') + text;
    current.isThinking = true;
    renderMessages();
}

function finalizeAssistantMessage(message) {
    if (!chatState.session) return;
    const messages = chatState.session.messages || [];
    const current = messages[messages.length - 1];
    if (current && current.role === 'assistant') {
        current.content = message?.content || current.content || '';
        current.streamingFinished = true;
        current.isThinking = false;
        current.tool_calls = message?.tool_calls || current.tool_calls || [];
        current.reasoning = message?.reasoning || current.reasoning || '';
    } else {
        messages.push({
            role: 'assistant',
            content: message?.content || '',
            tool_calls: message?.tool_calls || [],
            reasoning: message?.reasoning || ''
        });
    }
    renderMessages();
}

function appendToolCall(toolCall) {
    if (!toolCall || !chatState.currentStepId) return;
    const message = chatState.stepMessages[chatState.currentStepId];
    if (!message) return;
    message.toolCalls = message.toolCalls || [];
    message.toolCalls.push({ ...toolCall, status: 'running' });
    renderMessages();
}

function updateToolCallDelta(toolCall) {
    if (!toolCall) return;
    const stepId = chatState.currentStepId;
    if (!stepId) return;
    const message = chatState.stepMessages[stepId];
    if (!message || !message.toolCalls) return;
    const target = message.toolCalls.find((c) => c.id === toolCall.id);
    if (!target) return;
    target.arguments = toolCall.arguments;
    renderMessages();
}

function completeToolCall(toolCall) {
    if (!toolCall) return;
    
    // First try current step
    const stepId = chatState.currentStepId;
    if (stepId) {
        const message = chatState.stepMessages[stepId];
        if (message?.toolCalls) {
            const target = message.toolCalls.find((c) => c.id === toolCall.id);
            if (target) {
                target.status = 'completed';
                target.result = toolCall.result;
                renderMessages();
                return;
            }
        }
    }
    
    // Fall back to searching all messages
    const messages = chatState.session?.messages || [];
    for (const msg of messages) {
        const calls = msg.toolCalls || msg.tool_calls || [];
        const target = calls.find((c) => c.id === toolCall.id);
        if (target) {
            target.status = 'completed';
            target.result = toolCall.result;
            renderMessages();
            return;
        }
    }
}

async function refreshSessionState() {
    if (!chatState.sessionId) return;
    const latest = await fetchSession(chatState.sessionId);
    if (latest) {
        // Preserve tool calls from streaming stepMessages into latest session messages
        // The server doesn't always return full tool call details, so merge local state
        const localMessages = chatState.session?.messages || [];
        const serverMessages = latest.messages || [];
        
        // Merge tool calls from local streaming state into server state
        for (let i = 0; i < serverMessages.length && i < localMessages.length; i++) {
            const local = localMessages[i];
            const server = serverMessages[i];
            
            // Preserve tool calls if server doesn't have them
            if (local.toolCalls && local.toolCalls.length > 0 && !server.tool_calls?.length) {
                server.tool_calls = local.toolCalls;
            }
            // Preserve reasoning expanded state
            if (local.reasoning && !server.reasoning) {
                server.reasoning = local.reasoning;
            }
        }
        
        // Also merge any step messages that have tool calls
        for (const [stepId, stepMsg] of Object.entries(chatState.stepMessages)) {
            if (stepMsg.toolCalls && stepMsg.toolCalls.length > 0) {
                // Find matching message in server state
                const idx = serverMessages.findIndex(m => m.stepId === stepId || m.step);
                if (idx >= 0) {
                    serverMessages[idx].toolCalls = stepMsg.toolCalls;
                    serverMessages[idx].tool_calls = stepMsg.toolCalls;
                }
            }
        }
        
        chatState.session = latest;
        chatState.selectedSkillIds = Array.isArray(latest.skillIds) ? latest.skillIds.slice() : [];
        if (latest.provider && latest.model) {
            chatState.selectedModelKey = `${latest.provider}:${latest.model}`;
        }
        renderSession();
    }
}

function finalizeStreaming() {
    if (!chatState.streaming) return;
    chatState.streaming = false;
    abortStreaming();
    toggleFormDisabled(false);
    renderSession();
}

function abortStreaming() {
    const els = getChatElements();
    if (chatState.abortController) {
        chatState.abortController.abort();
    }
    chatState.abortController = null;
    chatState.streaming = false;
    if (els.stopBtn) els.stopBtn.disabled = true;
    toggleFormDisabled(false);
}

function toggleFormDisabled(disabled) {
    const els = getChatElements();
    if (els.textarea) els.textarea.disabled = disabled;
    if (els.modelSelect) els.modelSelect.disabled = disabled;
    if (els.modelTrigger) els.modelTrigger.disabled = disabled;
    if (els.providerFilterSelect) {
        els.providerFilterSelect.disabled = disabled || chatState.models.length === 0;
    }
    if (els.skillToggle) els.skillToggle.disabled = disabled;
    if (els.resetBtn) els.resetBtn.disabled = disabled;
    if (els.newSessionBtn) els.newSessionBtn.disabled = disabled;
    if (els.attachmentInput) els.attachmentInput.disabled = disabled;
    if (els.sendBtn) els.sendBtn.disabled = disabled;
}

function renderSession() {
    renderMessages();
    renderActiveSkills();
    renderSessionMeta();
    populateModelSelect();
    renderChatServerList();
}

function renderMessages() {
    const { messagesContainer } = getChatElements();
    if (!messagesContainer) return;
    messagesContainer.innerHTML = '';

    const messages = chatState.session?.messages || [];
    let renderedCount = 0;
    messages.forEach((message, msgIndex) => {
        const role = message.role || 'assistant';

        // Skip system messages - don't show in chat window
        if (role === 'system') return;
        
        // Skip tool role messages - results are shown in tool cards
        if (role === 'tool') return;

        // Create message block container
        const block = document.createElement('div');
        block.className = `chat-message-block chat-message-block--${role}`;

        const attachmentNode = renderMessageAttachments(message.attachments || []);
        if (attachmentNode) block.appendChild(attachmentNode);

        // Generate unique IDs for state tracking
        const reasoningId = `reasoning-${msgIndex}`;
        
        // 1. Render reasoning/thinking (Collapsed by default, persistent via chatState)
        if (role === 'assistant' && message.reasoning) {
            // Use persistent state from chatState, default to collapsed (false)
            const isExpanded = chatState.expandedReasoningIds.has(reasoningId);
            const reasoningEl = document.createElement('div');
            // Use chat-tool-call classes for consistent card styling
            reasoningEl.className = `chat-tool-call chat-reasoning ${isExpanded ? 'expanded' : ''} ${message.isThinking ? 'chat-reasoning--streaming' : ''}`;

            reasoningEl.innerHTML = `
                <div class="chat-tool-call__header">
                    <div class="chat-tool-call__name">
                        <svg class="chat-tool-call__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm0 18a8 8 0 1 1 8-8 8 8 0 0 1-8 8z"/>
                            <path d="M12 6v6l4 2"/>
                        </svg>
                        <span>Thinking</span>
                    </div>
                    <div class="chat-tool-call__status">
                         ${message.isThinking ? '<span class="chat-tool-call__dot"></span>' : ''}
                    </div>
                </div>
                <div class="chat-tool-call__body">
                    <div class="chat-reasoning__content">${escapeHtml(message.reasoning)}</div>
                </div>
            `;

            // Attach toggle listener using persistent state
            const header = reasoningEl.querySelector('.chat-tool-call__header');
            header.addEventListener('click', () => {
                if (chatState.expandedReasoningIds.has(reasoningId)) {
                    chatState.expandedReasoningIds.delete(reasoningId);
                } else {
                    chatState.expandedReasoningIds.add(reasoningId);
                }
                renderMessages();
            });

            block.appendChild(reasoningEl);
        }

        // 2. Render tool calls (Before content to allow [Tools] -> [Result/Text])
        const toolCalls = message.tool_calls || message.toolCalls || [];
        if (toolCalls.length) {
            const toolsContainer = document.createElement('div');
            toolsContainer.className = 'chat-tool-calls';

            toolCalls.forEach((call, callIndex) => {
                const toolEl = document.createElement('div');
                const status = call.status || (call.result ? 'success' : 'running');
                const isError = call.result?.ok === false || status === 'error';
                
                // Track tool expansion state
                const toolId = `tool-${msgIndex}-${callIndex}`;
                const isToolExpanded = chatState.expandedToolIds.has(toolId);

                toolEl.className = `chat-tool-call ${isError ? 'chat-tool-call--error' : status === 'running' ? 'chat-tool-call--running' : 'chat-tool-call--success'} ${isToolExpanded ? 'expanded' : ''}`;

                // Parse duration if available
                const duration = Number(call.result?.duration_ms || call.duration_ms);
                const durationText = Number.isFinite(duration) && duration >= 0 ? `${duration}ms` : '';

                toolEl.innerHTML = `
                    <div class="chat-tool-call__header">
                        <div class="chat-tool-call__name">
                            <svg class="chat-tool-call__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
                            </svg>
                            <span>${escapeHtml(call.name || call.function?.name || 'Tool')}</span>
                        </div>
                        <div class="chat-tool-call__status">
                            <div class="chat-tool-call__indicator">
                                <span class="chat-tool-call__dot"></span>
                                <span class="chat-tool-call__dot"></span>
                                <span class="chat-tool-call__dot"></span>
                            </div>
                            ${durationText ? `<span class="chat-tool-call__duration">${durationText}</span>` : ''}
                            <svg class="chat-tool-call__chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <polyline points="6 9 12 15 18 9"></polyline>
                            </svg>
                        </div>
                    </div>
                    <div class="chat-tool-call__body">
                        <div class="chat-tool-call__section">
                            <div class="chat-tool-call__label">Arguments</div>
                            <pre class="chat-tool-call__args">${formatToolArgs(call.arguments || call.function?.arguments)}</pre>
                        </div>
                        ${call.result ? `
                        <div class="chat-tool-call__section">
                            <div class="chat-tool-call__label">Result</div>
                            <pre class="chat-tool-call__result">${formatToolResult(call.result)}</pre>
                        </div>
                        ` : ''}
                    </div>
                `;

                // Add click handler for tool expansion toggle
                const toolHeader = toolEl.querySelector('.chat-tool-call__header');
                toolHeader.addEventListener('click', () => {
                    if (chatState.expandedToolIds.has(toolId)) {
                        chatState.expandedToolIds.delete(toolId);
                    } else {
                        chatState.expandedToolIds.add(toolId);
                    }
                    renderMessages();
                });

                toolsContainer.appendChild(toolEl);
            });

            block.appendChild(toolsContainer);
        }

        // 3. Render main message bubble (After tools)
        // Skip content that looks like raw tool result JSON (already shown in tool cards)
        const content = messageTextContent(message.content);
        const contentStr = content.trim();
        const isToolResultJson = contentStr.startsWith('{') && contentStr.endsWith('}') && 
            (contentStr.includes('"ok":') && contentStr.includes('"output":'));
        
        if (contentStr && !isToolResultJson) {
            const wrapper = document.createElement('div');
            wrapper.className = `chat-message ${role}`;

            const bubble = document.createElement('div');
            bubble.className = 'chat-bubble';
            bubble.innerHTML = formatMessageContent(content);
            wrapper.appendChild(bubble);

            block.appendChild(wrapper);
        }

        messagesContainer.appendChild(block);
        renderedCount += 1;
    });

    if (!renderedCount && !chatState.streaming) {
        const empty = document.createElement('div');
        empty.className = 'chat-empty-state';
        const title = document.createElement('strong');
        title.textContent = 'Start an agentic chat';
        const copy = document.createElement('span');
        copy.textContent = 'Choose a provider model, scope its tools and skills, then attach context or send a task.';
        empty.append(title, copy);
        messagesContainer.appendChild(empty);
    }

    // Add streaming indicator if currently streaming
    if (chatState.streaming) {
        const indicator = document.createElement('div');
        indicator.className = 'chat-streaming-indicator';
        indicator.innerHTML = `
            <div class="chat-streaming-dots">
                <span class="chat-streaming-dot"></span>
                <span class="chat-streaming-dot"></span>
                <span class="chat-streaming-dot"></span>
            </div>
            <span>Generating...</span>
        `;
        messagesContainer.appendChild(indicator);
    }

    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

function renderMessageAttachments(attachments) {
    if (!Array.isArray(attachments) || !attachments.length) return null;
    const container = document.createElement('div');
    container.className = 'chat-message-attachments chat-attachment-preview';
    attachments.forEach((attachment) => {
        const chip = document.createElement('div');
        chip.className = 'chat-attachment-chip';
        const icon = document.createElement('span');
        icon.className = 'chat-attachment-icon';
        icon.textContent = attachment.type === 'image'
            ? 'IMG'
            : attachment.type === 'file' ? 'PDF' : 'TXT';
        const copy = document.createElement('span');
        copy.className = 'chat-attachment-copy';
        const name = document.createElement('strong');
        name.textContent = attachment.name || 'Attachment';
        const detail = document.createElement('span');
        detail.textContent = attachment.sizeBytes
            ? formatChatBytes(attachment.sizeBytes)
            : (attachment.mimeType || 'Attached context');
        copy.append(name, detail);
        chip.append(icon, copy);
        container.appendChild(chip);
    });
    return container;
}

function messageTextContent(content) {
    if (typeof content === 'string') return content;
    if (Array.isArray(content)) {
        return content
            .filter((part) => part && part.type === 'text' && typeof part.text === 'string')
            .map((part) => part.text)
            .join('\n');
    }
    if (content === null || content === undefined) return '';
    if (typeof content === 'number' || typeof content === 'boolean') return String(content);
    if (typeof content.text === 'string') return content.text;
    return safeJson(content);
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function stripThinkTags(content) {
    // Strip <think>...</think> tags from content for display
    // These are preserved in message history for interleaved thinking
    if (!content) return content;
    return content.replace(/<think>[\s\S]*?<\/think>/g, '').trim();
}

function formatMessageContent(content) {
    if (!content) return '';
    // Strip <think> tags for display (kept in history for thinking continuity)
    let cleanContent = stripThinkTags(messageTextContent(content));
    // Basic markdown-like formatting
    let html = escapeHtml(cleanContent);
    // Code blocks
    html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Bold
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    // Line breaks
    html = html.replace(/\n/g, '<br>');
    return html;
}

function formatToolArgs(args) {
    if (!args) return escapeHtml('{}');
    try {
        let obj = args;
        if (typeof args === 'string') {
            obj = JSON.parse(args);
        }
        const json = JSON.stringify(obj, null, 2);
        return syntaxHighlightJson(json);
    } catch {
        return escapeHtml(String(args));
    }
}

function syntaxHighlightJson(json) {
    // Escape HTML first, then apply syntax highlighting
    const escaped = escapeHtml(json);
    // Apply syntax highlighting with spans
    return escaped
        // Strings (already escaped quotes)
        .replace(/(&quot;[^&]*&quot;)(\s*:)?/g, (match, str, colon) => {
            if (colon) {
                // Key
                return `<span class="json-key">${str}</span>${colon}`;
            }
            // Value string
            return `<span class="json-string">${str}</span>`;
        })
        // Numbers
        .replace(/\b(-?\d+\.?\d*)\b/g, '<span class="json-number">$1</span>')
        // Booleans and null
        .replace(/\b(true|false|null)\b/g, '<span class="json-boolean">$1</span>');
}

function formatToolResult(result) {
    if (!result) return '';
    try {
        if (typeof result === 'string') {
            // Plain string - escape and return
            return escapeHtml(result);
        }
        // Handle MCPO tool result format
        if (result.ok === false && result.error) {
            return `<span class="json-error">Error: ${escapeHtml(result.error)}</span>`;
        }
        if (result.output !== undefined) {
            if (typeof result.output === 'string') {
                // Check if it's JSON-like
                try {
                    const parsed = JSON.parse(result.output);
                    return syntaxHighlightJson(JSON.stringify(parsed, null, 2));
                } catch {
                    return escapeHtml(result.output);
                }
            }
            return syntaxHighlightJson(JSON.stringify(result.output, null, 2));
        }
        return syntaxHighlightJson(JSON.stringify(result, null, 2));
    } catch {
        return escapeHtml(String(result));
    }
}

function renderSessionMeta() {
    const { sessionMeta } = getChatElements();
    if (!sessionMeta) return;

    if (!chatState.session) {
        sessionMeta.textContent = 'No active session';
        return;
    }

    const sessionId = String(chatState.session.id || '').slice(0, 12);
    const provider = chatState.session.provider || 'unknown provider';
    const model = chatState.session.model || 'unknown model';
    const toolCount = chatState.session.tools?.length || 0;
    sessionMeta.textContent = `Session ${sessionId} · ${provider} / ${model} · ${toolCount} tools`;
}

function showChatAlert(message, variant = 'info') {
    const { alertsContainer } = getChatElements();
    if (!alertsContainer) return;

    const node = document.createElement('div');
    const safeVariant = ['info', 'success', 'error', 'warning'].includes(variant)
        ? variant
        : 'info';
    node.className = `chat-alert ${safeVariant}`;
    const text = document.createElement('span');
    text.textContent = getChatErrorMessage(message, 'Request failed');
    node.appendChild(text);

    alertsContainer.prepend(node);
    setTimeout(() => {
        if (alertsContainer.contains(node)) {
            alertsContainer.removeChild(node);
        }
    }, 5000);
}

function safeJson(value) {
    if (!value) return '';
    try {
        if (typeof value === 'string') {
            JSON.parse(value);
            return value;
        }
        return JSON.stringify(value, null, 2);
    } catch (error) {
        return String(value);
    }
}

function appendStepMessage(step) {
    if (!step) return;
    const message = {
        role: 'assistant',
        step: true,
        stepId: step.id,
        title: step.title || step.type || 'Agent step',
        content: step.detail?.summary || step.title || 'Agent step in progress…',
        finishReason: step.detail?.finishReason || null,
        summary: step.detail?.summary || null,
        toolCalls: [],
    };
    chatState.session = chatState.session || { messages: [] };
    chatState.session.messages = chatState.session.messages || [];
    chatState.session.messages.push(message);
    chatState.stepMessages[step.id] = message;
    chatState.currentStepId = step.id;
    renderMessages();
}

function updateStepMessage(step, status) {
    if (!step) return;
    const message = chatState.stepMessages[step.id];
    if (!message) return;
    message.finishReason = step.detail?.finishReason || status || null;
    if (step.detail?.summary) {
        message.summary = step.detail.summary;
        message.content = step.detail.summary;
    }
    renderMessages();
}

window.initChatPage = initChatPage;
