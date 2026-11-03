"use strict";
async function loadSettings(showNotice) {
    setSettingsBusy("loading");
    try {
        state.settings = await API.get("/settings");
        state.settingsApiProfileId = state.settings.active_api_profile || "";
        state.settingsDirty = false;
        state.settingsConnectionDirty = false;
        loadPersistedModelStatuses();
        initializeModelCatalog();
        renderSettings(); await loadModelBenchmark();
        if (showNotice) toast("设置已从 .env 重新载入", "success");
    } catch (error) {
        toast(error.message, "error");
    } finally {
        setSettingsBusy("");
    }
}
function initializeModelCatalog() {
    var settings = state.settings || {};
    var api = settings.api || {};
    var ollama = settings.ollama || {};
    var apiDisabled = new Set(api.disabled_models || []);
    var apiProtocols = api.model_protocols || {};
    var localDisabled = new Set(ollama.disabled_models || []);
    state.modelCatalog.api = (api.models || []).map(function (name) {
        return modelCatalogEntry("api", name, !apiDisabled.has(name), apiProtocols[name] || "");
    });
    var localNames = ((state.models || []).filter(function (model) {
        return model.provider === "ollama";
    }).map(function (model) {
        return modelLabel(model.id || model.name);
    })).concat(Array.from(localDisabled));
    state.modelCatalog.ollama = Array.from(new Set(localNames)).map(function (name) {
        return modelCatalogEntry("ollama", name, !localDisabled.has(name));
    });
    var configured = String(settings.default_model || "");
    var parts = configured.split(":", 2);
    if (parts.length === 2 && parts[1] && state.modelCatalog[parts[0]]
        && !state.modelCatalog[parts[0]].some(function (item) { return item.id === configured; })) {
        state.modelCatalog[parts[0]].push(modelCatalogEntry(
            parts[0],
            parts[1],
            !(parts[0] === "api" ? apiDisabled : localDisabled).has(parts[1])
        ));
    }
}
function modelCatalogEntry(provider, name, enabled, protocol) {
    var clean = String(name || "").replace(/^(api|ollama):/, "").trim();
    return {
        id: provider + ":" + clean,
        name: clean,
        provider: provider,
        enabled: enabled !== false,
        protocol: String(protocol || ""),
    };
}
function renderSettings() {
    if (!state.settings) return;
    var settings = state.settings;
    var api = settings.api || {};
    var ollama = settings.ollama || {};
    var file = settings.file || {};
    el("settings-provider").value = settings.provider || "api";
    state.settingsApiProfileId = settings.active_api_profile || "";
    renderProviderPicker();
    el("settings-api-style").value = api.style || "opencode_go";
    updateApiProtocolOptions();
    el("settings-base-url").value = api.base_url || "";
    el("settings-ollama-host").value = ollama.host || "http://localhost:11434";
    el("settings-disable-thinking").checked = api.disable_thinking !== false;
    el("settings-api-key").value = "";
    el("settings-clear-key").checked = false;
    el("settings-key-state").textContent = api.api_key_configured
        ? "已配置（来源：" + keySourceLabel(api.api_key_source) + "）"
        : "尚未配置";
    el("settings-connect-note").textContent = api.api_key_configured && (api.models || []).length
        ? "连接已保存。可以刷新目录，或直接使用默认模型。"
        : "只读取模型目录，不会发送翻译或模型测试请求。";
    el("settings-file-state").className = "settings-file-state" + (file.writable ? "" : " error");
    el("settings-file-state").textContent = "配置文件：" + (file.path || "未知")
        + " · " + (file.exists ? "已存在" : "保存时创建")
        + " · " + (file.writable ? "可写" : "不可写")
        + " · " + (settings.precedence_note || "");
    renderSettingsModelPicker();
    el("settings-default-model").value = settings.default_model || "";
    renderSettingsStatus();
    updateSettingsControls();
}
function activeApiProviderName() {
    var settings = state.settings || {};
    var profile = (settings.api_profiles || []).find(function (item) {
        return item.id === settings.active_api_profile;
    });
    var runtimeApi = state.runtime && state.runtime.providers && state.runtime.providers.api || {};
    return profile && profile.name || (settings.api || {}).provider_name || runtimeApi.provider_name || "联网 API";
}

function renderProviderPicker() {
    var settings = state.settings || {};
    var profiles = settings.api_profiles || [];
    var current = profiles.find(function (item) { return item.id === state.settingsApiProfileId; });
    el("settings-api-profile").innerHTML = profiles.map(function (item) {
        return '<option value="' + escapeHtml(item.id) + '">' + escapeHtml(item.name)
            + (item.id === settings.active_api_profile ? " · 当前启用" : "") + "</option>";
    }).join("");
    el("settings-api-profile").value = settings.active_api_profile || "";
    el("settings-api-preset").innerHTML = (settings.provider_presets || []).map(function (item) {
        return '<option value="' + escapeHtml(item.id) + '">' + escapeHtml(item.name) + "</option>";
    }).join("");
    el("settings-api-preset").value = current && current.preset || (settings.api || {}).preset || "openai";
    el("settings-profile-name").value = current && current.name || activeApiProviderName();
    renderActiveProviderNote();
}

function renderActiveProviderNote() {
    var creating = state.settingsApiProfileId !== (state.settings || {}).active_api_profile;
    el("settings-active-provider").textContent = "当前启用：" + activeApiProviderName()
        + (creating ? " · 正在添加新连接" : " · 选择已保存配置即可切换");
}

function updateApiProtocolOptions() {
    var isOpenCode = el("settings-api-preset").value === "opencode_go";
    el("settings-opencode-protocol").hidden = !isOpenCode;
    el("settings-opencode-protocol").disabled = !isOpenCode;
    if (!isOpenCode && el("settings-api-style").value === "opencode_go") {
        el("settings-api-style").value = "openai";
    }
}

function newApiProvider() {
    if (state.settingsDirty) return;
    state.settingsApiProfileId = "provider-" + Date.now().toString(36);
    el("settings-provider").value = "api";
    el("settings-api-profile").value = "";
    el("settings-api-preset").value = "opendesign_go";
    applyProviderPreset(true);
    renderActiveProviderNote();
}

function applyProviderPreset(creating) {
    var preset = ((state.settings || {}).provider_presets || []).find(function (item) {
        return item.id === el("settings-api-preset").value;
    });
    if (!preset) return;
    // A different endpoint never inherits a saved key, model catalog, or tests.
    // When editing an existing configuration, use a new profile to retain it.
    if (!creating && state.settingsApiProfileId === (state.settings || {}).active_api_profile) {
        state.settingsApiProfileId = "provider-" + Date.now().toString(36);
        el("settings-api-profile").value = "";
    }
    el("settings-profile-name").value = preset.name;
    el("settings-api-style").value = preset.style;
    updateApiProtocolOptions();
    el("settings-base-url").value = preset.base_url || "";
    el("settings-disable-thinking").checked = preset.disable_thinking !== false;
    el("settings-api-key").value = "";
    el("settings-clear-key").checked = false;
    el("settings-key-state").textContent = "新配置尚未设置密钥";
    state.modelCatalog.api = [];
    state.modelAvailability = {};
    state.modelNsfwAvailability = {};
    el("settings-default-model").value = "";
    el("settings-model-search").value = "";
    renderSettingsModelPicker();
    renderActiveProviderNote();
    markSettingsDirty(true);
}

async function activateApiProvider() {
    if (state.settingsDirty || state.settingsBusy || settingsTaskIsActive() || benchmarkIsActive()) return;
    var selected = el("settings-api-profile").value;
    if (!selected || selected === (state.settings || {}).active_api_profile) return;
    setSettingsBusy("switching");
    try {
        var result = await API.post("/settings/providers/activate", { profile_id: selected });
        state.settings = result.settings;
        state.settingsApiProfileId = state.settings.active_api_profile;
        state.settingsDirty = false;
        state.settingsConnectionDirty = false;
        loadPersistedModelStatuses();
        initializeModelCatalog();
        state.modelBenchmark = null;
        await loadModels(false);
        await loadModelBenchmark();
        renderSettings();
        invalidatePreflight();
        toast("已启用 " + activeApiProviderName(), "success");
    } catch (error) {
        el("settings-api-profile").value = (state.settings || {}).active_api_profile || "";
        toast(error.message, "error");
    } finally {
        setSettingsBusy("");
    }
}
function keySourceLabel(source) {
    return {
        process_environment: "进程环境变量",
        env_file: ".env 文件",
        none: "未配置",
    }[source] || source || "未知";
}

function currentSettingsProvider() {
    return el("settings-provider").value || "api";
}

function settingsTaskIsActive() {
    return ["starting", "running", "paused", "stopping", "preparing", "finalizing"].includes(state.taskStatus)
        || (typeof aiReviewIsActive === "function" && aiReviewIsActive());
}

function currentModelCatalog() {
    return state.modelCatalog[currentSettingsProvider()] || [];
}

function renderSettingsModelPicker() {
    var provider = currentSettingsProvider();
    var query = el("settings-model-search").value.trim().toLowerCase();
    var catalog = currentModelCatalog();
    var visible = catalog.filter(function (item) {
        return !query || item.name.toLowerCase().includes(query);
    });
    renderModelPickerMetadata();
    el("settings-model-list").innerHTML = visible.length
        ? visible.map(renderModelRow).join("")
        : '<div class="model-empty">点击“连接并获取模型”读取目录；也可以手动添加模型 ID。</div>';
}

function renderModelPickerMetadata() {
    var provider = currentSettingsProvider();
    var catalog = currentModelCatalog();
    var enabled = catalog.filter(function (item) { return item.enabled; });
    ["settings-api-profiles-field", "settings-profile-name-field", "settings-preset-field"].forEach(function (id) {
        el(id).hidden = provider !== "api";
    });
    el("settings-key-field").hidden = provider !== "api";
    el("settings-model-summary").textContent = enabled.length + " / " + catalog.length
        + " 个模型已启用 · " + (provider === "api" ? el("settings-profile-name").value || activeApiProviderName() : "本地 Ollama");
    var selected = el("settings-default-model").value;
    el("settings-default-model").innerHTML = '<option value="">' + (enabled.length ? "请选择默认模型" : "请先获取或添加模型") + '</option>'
        + enabled.map(function (item) {
            return '<option value="' + escapeHtml(item.id) + '">' + escapeHtml(item.name) + "</option>";
        }).join("");
    el("settings-default-model").value = enabled.some(function (item) { return item.id === selected; }) ? selected : "";
}

function renderModelRow(item) {
    var basicRecord = modelTestRecord(state.modelAvailability, item.id);
    var nsfwRecord = modelTestRecord(state.modelNsfwAvailability, item.id);
    var availability = basicRecord.status;
    var nsfwAvailability = nsfwRecord.status;
    var testDisabled = state.settingsConnectionDirty || Boolean(state.settingsBusy)
        || settingsTaskIsActive();
    var status = basicRecord.stale ? {
        available: "上次可用",
        unavailable: "上次不可用",
        error: "上次失败",
    }[availability] : {
        untested: "未测试",
        testing: "测试中",
        available: "可用",
        unavailable: "不可用",
    }[availability];
    var nsfwStatus = nsfwRecord.stale ? {
        available: "NSFW 上次可用",
        restricted: "NSFW 上次受限",
        error: "NSFW 上次失败",
    }[nsfwAvailability] : {
        untested: "NSFW 未测试",
        testing: "NSFW 测试中",
        available: "NSFW 可用",
        restricted: "NSFW 受限",
        error: "NSFW 测试失败",
    }[nsfwAvailability];
    var protocolPicker = item.provider === "api"
        ? '<select class="model-protocol-select" data-model-protocol="' + escapeHtml(item.id) + '" aria-label="' + escapeHtml(item.name) + ' 协议">'
        + '<option value=""' + (!item.protocol ? " selected" : "") + '>协议：自动</option><option value="responses"' + (item.protocol === "responses" ? " selected" : "") + '>Responses</option>'
        + '<option value="messages"' + (item.protocol === "messages" ? " selected" : "") + '>Messages</option><option value="chat_completions"' + (item.protocol === "chat_completions" ? " selected" : "") + '>Chat</option></select>'
        : "";
    return '<div class="model-row" data-model-row="' + escapeHtml(item.id) + '">'
        + '<input type="checkbox" data-model-enabled="' + escapeHtml(item.id) + '"'
        + (item.enabled ? " checked" : "") + ' aria-label="启用 ' + escapeHtml(item.name) + '">'
        + '<span class="model-row-copy"><span class="model-row-name" title="' + escapeHtml(item.name)
        + '">' + escapeHtml(item.name) + '</span><small>' + escapeHtml(modelTestTimeSummary(basicRecord, nsfwRecord))
        + "</small></span>"
        + '<span class="availability-badge ' + availability + (basicRecord.stale ? " stale" : "") + '">' + status + "</span>"
        + '<span class="availability-badge ' + nsfwAvailability + (nsfwRecord.stale ? " stale" : "") + '">' + nsfwStatus + "</span>"
        + protocolPicker
        + '<button type="button" class="btn btn-secondary btn-sm" data-test-model="' + escapeHtml(item.id) + '"'
        + (testDisabled ? " disabled" : "") + ">"
        + (availability === "testing" ? "测试中…" : "测试可用性") + "</button>"
        + '<button type="button" class="btn btn-secondary btn-sm" data-test-model-nsfw="' + escapeHtml(item.id) + '"'
        + (testDisabled ? " disabled" : "") + ">"
        + (nsfwAvailability === "testing" ? "测试中…" : "测试 NSFW") + "</button></div>";
}

function renderSettingsStatus() {
    if (!state.runtime) return;
    var providers = state.runtime.providers || {};
    var providerRows = [
        {
            name: activeApiProviderName(),
            detail: providers.api ? providers.api.health_note : "未配置",
            good: providers.api && providers.api.configured,
            label: providers.api && providers.api.configured ? "配置完整" : "配置不完整",
        },
        {
            name: "Ollama",
            detail: providers.ollama ? providers.ollama.health_note : "未连接",
            good: providers.ollama && providers.ollama.configured,
            label: providers.ollama && providers.ollama.configured
                ? providers.ollama.model_count + " 个模型" : "离线",
        },
    ];
    el("provider-status-list").innerHTML = providerRows.map(function (row) {
        return '<div class="provider-status-item"><div class="provider-copy"><strong>'
            + escapeHtml(row.name) + "</strong><span>" + escapeHtml(row.detail || "")
            + '</span></div><span class="health-badge ' + (row.good ? "good" : "bad")
            + '">' + escapeHtml(row.label) + "</span></div>";
    }).join("");
    el("settings-profile-list").innerHTML = (state.runtime.profiles || []).map(function (profile) {
        return '<div class="settings-profile-item"><div class="settings-profile-copy"><strong>'
            + escapeHtml(profile.name) + (profile.recommended ? " · 推荐" : "")
            + "</strong><span>" + escapeHtml(profile.description || "")
            + '</span></div><span class="health-badge ' + (profile.available ? "good" : "bad")
            + '">' + (profile.available ? "可用" : "不可用") + "</span></div>";
    }).join("");
}

function settingsValidationMessage(connectionOnly) {
    if (!state.settings) return "设置尚未加载";
    var provider = currentSettingsProvider();
    var defaultModel = el("settings-default-model").value.trim();
    var emptyApiCatalog = provider === "api" && !currentModelCatalog().length && !defaultModel;
    if (!connectionOnly && !emptyApiCatalog && !defaultModel.startsWith(provider + ":")) return "请选择当前提供商的默认模型";
    var selected = currentModelCatalog().find(function (item) {
        return item.id === defaultModel && item.enabled;
    });
    if (!connectionOnly && !selected && !emptyApiCatalog) return "请选择一个已启用的默认模型";
    if (provider === "api") {
        if (!el("settings-profile-name").value.trim()) return "请填写提供商配置名称";
        var editingActive = state.settingsApiProfileId === state.settings.active_api_profile;
        var hasKey = (editingActive && (state.settings.api || {}).api_key_configured)
            || Boolean(el("settings-api-key").value.trim());
        if (!hasKey || el("settings-clear-key").checked) return "联网 API 需要有效密钥";
        if (el("settings-api-style").value !== "opencode_go"
            && !el("settings-base-url").value.trim()) return "兼容 API 需要 Base URL";
    }
    return "";
}

function updateSettingsControls() {
    var active = settingsTaskIsActive(), operationsBlocked = active || benchmarkIsActive();
    var busy = Boolean(state.settingsBusy);
    var writable = !state.settings || !state.settings.file || state.settings.file.writable;
    var validation = settingsValidationMessage();
    el("settings-fieldset").disabled = operationsBlocked || busy;
    el("settings-dirty-badge").className = "status-badge "
        + (busy ? "running" : state.settingsDirty ? "warning" : "neutral");
    el("settings-dirty-badge").textContent = busy
        ? { loading: "正在加载", saving: "正在保存", connecting: "正在连接并读取模型", discovering: "正在获取模型", testing: "正在测试模型", switching: "正在切换提供商" }[state.settingsBusy]
        : state.settingsDirty ? "有未保存修改" : "尚未修改";
    el("btn-settings-refresh").disabled = busy;
    el("settings-api-profile").disabled = operationsBlocked || busy || state.settingsDirty;
    el("btn-provider-new").disabled = operationsBlocked || busy || state.settingsDirty;
    var connectionValidation = settingsValidationMessage(true);
    el("btn-settings-connect").disabled = operationsBlocked || busy || !writable || Boolean(connectionValidation);
    el("btn-settings-discover").disabled = el("btn-settings-connect").disabled;
    var hasDefault = Boolean(el("settings-default-model").value);
    el("btn-settings-save").disabled = operationsBlocked || busy || !writable || !hasDefault || Boolean(validation);
    var noEnabledModels = !currentModelCatalog().some(function (item) { return item.enabled; });
    el("btn-test-enabled-models").disabled = operationsBlocked || busy || state.settingsConnectionDirty || noEnabledModels;
    el("btn-test-enabled-nsfw").disabled = operationsBlocked || busy || state.settingsConnectionDirty || noEnabledModels;
    all("[data-test-model], [data-test-model-nsfw]", el("settings-model-list")).forEach(function (button) {
        button.disabled = operationsBlocked || busy || state.settingsConnectionDirty;
    });
    el("settings-save-note").textContent = active
        ? "翻译或 AI 复核活动期间不能修改、获取或测试模型。"
        : busy ? "请等待当前设置操作完成。"
        : !writable ? "配置目录不可写；请把软件移动到可写目录。"
        : validation || (state.settingsConnectionDirty
            ? "点击“连接并获取模型”，会保存连接并读取模型目录。"
            : state.settingsDirty
                ? "选好默认模型后，点击“完成并使用”保存选择。"
                : "连接已就绪。可直接使用，或展开下方可选测试。");
}

function markSettingsDirty(connectionChanged) {
    state.settingsDirty = true;
    state.settingsConnectionDirty = state.settingsConnectionDirty || Boolean(connectionChanged);
    updateSettingsControls(); renderModelBenchmark();
}
function setSettingsBusy(mode) {
    state.settingsBusy = mode || "";
    if (el("settings-fieldset")) { updateSettingsControls(); renderModelBenchmark(); }
}

function settingsPayload() {
    return {
        provider: currentSettingsProvider(),
        api_profile_id: state.settingsApiProfileId || undefined,
        api_profile_name: el("settings-profile-name").value.trim(),
        api_preset: el("settings-api-preset").value,
        api_style: el("settings-api-style").value,
        api_base_url: el("settings-base-url").value.trim(),
        api_models: state.modelCatalog.api.map(function (item) { return item.name; }),
        disabled_api_models: state.modelCatalog.api.filter(function (item) {
            return !item.enabled;
        }).map(function (item) { return item.name; }),
        api_model_protocols: Object.fromEntries(state.modelCatalog.api.filter(function (item) { return Boolean(item.protocol); })
            .map(function (item) { return [item.name, item.protocol]; })),
        disabled_ollama_models: state.modelCatalog.ollama.filter(function (item) {
            return !item.enabled;
        }).map(function (item) { return item.name; }),
        default_model: el("settings-default-model").value.trim(),
        disable_thinking: el("settings-disable-thinking").checked,
        ollama_host: el("settings-ollama-host").value.trim(),
        api_key_action: el("settings-clear-key").checked ? "clear"
            : el("settings-api-key").value.trim() ? "replace" : "keep",
        api_key: el("settings-api-key").value.trim(),
    };
}

async function saveSettings(event) {
    event.preventDefault();
    setSettingsBusy("saving");
    try {
        var result = await API.put("/settings", settingsPayload());
        state.settings = result.settings;
        state.settingsApiProfileId = state.settings.active_api_profile || "";
        state.settingsDirty = false;
        state.settingsConnectionDirty = false;
        loadPersistedModelStatuses();
        initializeModelCatalog();
        state.modelBenchmark = null;
        await loadModels(false);
        await loadModelBenchmark();
        renderSettings();
        invalidatePreflight();
        toast("模型已就绪，可以开始翻译", "success");
        if (typeof navigate === "function") navigate("translate");
    } catch (error) {
        toast(error.message, "error");
    } finally {
        setSettingsBusy("");
    }
}

async function connectSettingsProvider() {
    if (state.settingsBusy || settingsTaskIsActive() || benchmarkIsActive()) return;
    var validation = settingsValidationMessage(true);
    if (validation) { toast(validation, "error"); return; }
    setSettingsBusy("connecting");
    try {
        var previousDefault = el("settings-default-model").value;
        var provider = currentSettingsProvider();
        // Save the connection first so discovery uses the exact entered key,
        // endpoint and protocol. Model selection remains the final user step.
        if (state.settingsConnectionDirty || provider !== (state.settings || {}).provider) {
            var payload = settingsPayload();
            if (provider === "api") {
                payload.api_models = [];
                payload.disabled_api_models = [];
                payload.api_model_protocols = {};
                payload.default_model = "";
            } else {
                payload.default_model = previousDefault || "ollama:";
            }
            var saved = await API.put("/settings", payload);
            state.settings = saved.settings;
            state.settingsApiProfileId = state.settings.active_api_profile || "";
            state.settingsDirty = false;
            state.settingsConnectionDirty = false;
            loadPersistedModelStatuses();
            initializeModelCatalog();
            renderSettings();
        }
        var result = await API.post("/settings/models/discover", { provider: provider });
        var previous = new Map(currentModelCatalog().map(function (item) { return [item.id, item]; }));
        state.modelCatalog[provider] = (result.models || []).map(function (item) {
            var old = previous.get(item.id);
            return modelCatalogEntry(provider, item.name, old ? old.enabled : true, old ? old.protocol : "");
        });
        state.settingsDirty = true;
        renderSettingsModelPicker();
        if (currentModelCatalog().some(function (item) { return item.id === previousDefault && item.enabled; })) {
            el("settings-default-model").value = previousDefault;
        }
        state.modelBenchmark = null;
        await loadModels(false);
        await loadModelBenchmark();
        invalidatePreflight();
        el("settings-connect-note").textContent = result.ok
            ? "已读取 " + result.models.length + " 个模型。选择默认模型后点击“完成并使用”。"
            : "实时目录暂不可用，已保留原有模型；也可以手动添加模型 ID。";
        toast(result.ok ? "连接成功，请选择默认模型" : result.warning || "模型目录读取失败", result.ok ? "success" : "error");
    } catch (error) {
        toast(error.message, "error");
        el("settings-connect-note").textContent = "连接尚未完成：" + error.message + "。可重试或展开模型管理手动添加模型。";
        state.modelBenchmark = null;
        await loadModels(false);
        await loadModelBenchmark();
    } finally {
        setSettingsBusy("");
    }
}

function setAllCurrentModels(enabled) {
    currentModelCatalog().forEach(function (item) { item.enabled = enabled; });
    renderSettingsModelPicker();
    markSettingsDirty(false);
}

function addSettingsModel() {
    var raw = el("settings-model-manual").value.trim();
    var provider = currentSettingsProvider();
    if (!raw || /[\s,]/.test(raw)) { toast("请填写单个有效模型 ID", "error"); return; }
    var item = modelCatalogEntry(provider, raw, true);
    if (currentModelCatalog().some(function (entry) { return entry.id === item.id; })) {
        toast("该模型已在列表中", "error"); return;
    }
    state.modelCatalog[provider].push(item);
    el("settings-model-manual").value = "";
    renderSettingsModelPicker();
    if (!el("settings-default-model").value) el("settings-default-model").value = item.id;
    markSettingsDirty(false);
}

function bindSettingsEvents() {
    bindModelBenchmarkEvents();
    el("settings-form").addEventListener("submit", saveSettings);
    el("settings-api-profile").addEventListener("change", activateApiProvider);
    el("btn-provider-new").addEventListener("click", newApiProvider);
    el("settings-api-preset").addEventListener("change", function () { applyProviderPreset(false); });
    el("settings-profile-name").addEventListener("input", function () { markSettingsDirty(false); });
    el("btn-model-add").addEventListener("click", addSettingsModel);
    el("settings-model-manual").addEventListener("keydown", function (event) {
        if (event.key === "Enter") { event.preventDefault(); addSettingsModel(); }
    });
    el("btn-settings-refresh").addEventListener("click", function () { loadSettings(true); });
    el("btn-settings-connect").addEventListener("click", connectSettingsProvider);
    el("btn-settings-discover").addEventListener("click", connectSettingsProvider);
    el("btn-test-enabled-models").addEventListener("click", function () {
        testEnabledModels("basic");
    });
    el("btn-test-enabled-nsfw").addEventListener("click", function () {
        testEnabledModels("adult");
    });
    el("btn-model-select-all").addEventListener("click", function () { setAllCurrentModels(true); });
    el("btn-model-select-none").addEventListener("click", function () { setAllCurrentModels(false); });
    el("settings-model-search").addEventListener("input", renderSettingsModelPicker);
    el("settings-model-list").addEventListener("change", function (event) {
        var protocolSelect = event.target.closest("[data-model-protocol]");
        if (protocolSelect) {
            var protocolItem = currentModelCatalog().find(function (entry) { return entry.id === protocolSelect.dataset.modelProtocol; });
            if (protocolItem) protocolItem.protocol = protocolSelect.value;
            markSettingsDirty(false);
            return;
        }
        var checkbox = event.target.closest("[data-model-enabled]");
        if (!checkbox) return;
        var item = currentModelCatalog().find(function (entry) {
            return entry.id === checkbox.dataset.modelEnabled;
        });
        if (item) item.enabled = checkbox.checked;
        renderModelPickerMetadata();
        markSettingsDirty(false);
    });
    el("settings-model-list").addEventListener("click", function (event) {
        var nsfwButton = event.target.closest("[data-test-model-nsfw]");
        if (nsfwButton) {
            testModelNsfwAvailability(nsfwButton.dataset.testModelNsfw);
            return;
        }
        var button = event.target.closest("[data-test-model]");
        if (button) testModelAvailability(button.dataset.testModel);
    });
    el("settings-provider").addEventListener("change", function () {
        el("settings-model-search").value = "";
        renderSettingsModelPicker();
        markSettingsDirty(false);
    });
    el("settings-default-model").addEventListener("change", function () { markSettingsDirty(false); });
    ["settings-api-style", "settings-base-url", "settings-api-key",
        "settings-ollama-host", "settings-disable-thinking", "settings-clear-key"].forEach(function (id) {
        el(id).addEventListener("input", function () {
            if (id === "settings-api-key" && this.value) el("settings-clear-key").checked = false;
            if (id === "settings-clear-key" && this.checked) el("settings-api-key").value = "";
            markSettingsDirty(true);
        });
        el(id).addEventListener("change", function () { markSettingsDirty(true); });
    });
}
