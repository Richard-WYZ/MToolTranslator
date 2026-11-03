"use strict";
async function loadSettings(showNotice) {
    if (typeof settingsAutosavePromise !== "undefined" && settingsAutosavePromise) await settingsAutosavePromise;
    setSettingsBusy("loading");
    try {
        state.settings = await API.get("/settings");
        state.settingsApiProfileId = state.settings.active_api_profile || "";
        state.settingsDirty = false;
        state.settingsConnectionDirty = false;
        if (typeof resetSettingsAutosave === "function") resetSettingsAutosave();
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
    var apiEndpoints = api.model_endpoints || {};
    var localDisabled = new Set(ollama.disabled_models || []);
    state.modelCatalog.api = (api.models || []).map(function (name) {
        return modelCatalogEntry("api", name, !apiDisabled.has(name), "", apiEndpoints[name] || []);
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
function modelCatalogEntry(provider, name, enabled, protocol, endpoints) {
    var clean = String(name || "").replace(/^(api|ollama):/, "").trim();
    return {
        id: provider + ":" + clean,
        name: clean,
        provider: provider,
        enabled: enabled !== false,
        protocol: String(protocol || ""),
        supported_endpoint_types: Array.isArray(endpoints) ? endpoints.slice() : [],
    };
}
function renderSettings() {
    if (!state.settings) return;
    var settings = state.settings;
    var api = settings.api || {};
    var ollama = settings.ollama || {};
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
        ? "已配置" : "未配置";
    el("settings-connect-note").textContent = !api.api_key_configured ? "尚未连接"
        : (api.models || []).length ? "已读取 " + api.models.length + " 个模型" : "";
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
    el("settings-active-provider").textContent = creating ? "新增提供商" : activeApiProviderName();
}

function updateApiProtocolOptions() {
    var isOpenCode = el("settings-api-preset").value === "opencode_go";
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
    markSettingsDirty(true, true);
}

async function activateApiProvider() {
    if (state.settingsBusy || settingsTaskIsActive() || benchmarkIsActive()) return;
    if (typeof settingsHasSecretEdits === "function" && settingsHasSecretEdits()) { toast("请先确认保存或放弃密钥修改", "error"); return; }
    var selected = el("settings-api-profile").value;
    if (typeof flushSettingsAutosave === "function" && !await flushSettingsAutosave()) return;
    if (!selected || selected === (state.settings || {}).active_api_profile) return;
    setSettingsBusy("switching");
    try {
        var result = await API.post("/settings/providers/activate", { profile_id: selected });
        state.settings = result.settings;
        state.settingsApiProfileId = state.settings.active_api_profile;
        state.settingsDirty = false;
        state.settingsConnectionDirty = false;
        if (typeof resetSettingsAutosave === "function") resetSettingsAutosave();
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
    el("settings-default-model").value = enabled.some(function (item) { return item.id === selected; })
        ? selected : selected && enabled.length ? enabled[0].id : "";
}

function renderModelRow(item) {
    var basicRecord = modelTestRecord(state.modelAvailability, item.id);
    var nsfwRecord = modelTestRecord(state.modelNsfwAvailability, item.id);
    var availability = basicRecord.status;
    var nsfwAvailability = nsfwRecord.status;
    var testDisabled = Boolean(state.settingsBusy)
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
    return '<div class="model-row" data-model-row="' + escapeHtml(item.id) + '">'
        + '<input type="checkbox" data-model-enabled="' + escapeHtml(item.id) + '"'
        + (item.enabled ? " checked" : "") + ' aria-label="启用 ' + escapeHtml(item.name) + '">'
        + '<span class="model-row-copy"><span class="model-row-name" title="' + escapeHtml(item.name)
        + '">' + escapeHtml(item.name) + '</span><small>' + escapeHtml(modelTestTimeSummary(basicRecord, nsfwRecord))
        + "</small></span>"
        + '<span class="availability-badge ' + availability + (basicRecord.stale ? " stale" : "") + '">' + status + "</span>"
        + '<span class="availability-badge ' + nsfwAvailability + (nsfwRecord.stale ? " stale" : "") + '">' + nsfwStatus + "</span>"
        + '<button type="button" class="btn btn-secondary btn-sm" data-test-model="' + escapeHtml(item.id) + '"'
        + (testDisabled ? " disabled" : "") + ">"
        + (availability === "testing" ? "测试中…" : "测试可用性") + "</button>"
        + '<button type="button" class="btn btn-secondary btn-sm" data-test-model-nsfw="' + escapeHtml(item.id) + '"'
        + (testDisabled ? " disabled" : "") + ">"
        + (nsfwAvailability === "testing" ? "测试中…" : "测试 NSFW") + "</button></div>";
}

function renderSettingsStatus() {
    // Runtime state is rendered in the translation workspace. This hook stays
    // available to refresh callers without rebuilding configuration help cards.
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
        if (!hasKey) return "联网 API 需要有效密钥";
        if (el("settings-api-style").value !== "opencode_go"
            && !el("settings-base-url").value.trim()) return "兼容 API 需要 Base URL";
    }
    return "";
}

function updateSettingsControls() {
    var active = settingsTaskIsActive(), operationsBlocked = active || benchmarkIsActive();
    var busy = Boolean(state.settingsBusy);
    var writable = !state.settings || !state.settings.file || state.settings.file.writable;
    var secretDirty = Boolean(el("settings-api-key").value.trim() || el("settings-clear-key").checked);
    var autosaving = Boolean(state.settingsAutosaving);
    var ordinaryError = typeof settingsAutosaveError === "string" ? settingsAutosaveError : "";
    var saveError = ordinaryError || state.settingsSecretError || "";
    el("settings-fieldset").disabled = operationsBlocked || busy;
    el("settings-dirty-badge").className = "status-badge "
        + (busy || autosaving ? "running" : saveError || secretDirty ? "warning" : "neutral");
    el("settings-dirty-badge").textContent = busy
        ? { loading: "正在加载", saving: "正在保存", connecting: "正在连接并读取模型", discovering: "正在获取模型", testing: "正在测试模型", switching: "正在切换提供商" }[state.settingsBusy]
        : autosaving ? "自动保存中" : saveError ? "保存失败" : secretDirty ? "密钥待确认" : state.settingsDirty ? "待自动保存" : "已保存";
    el("btn-settings-refresh").disabled = busy || autosaving;
    el("settings-api-profile").disabled = operationsBlocked || busy || autosaving || state.settingsDirty;
    el("btn-provider-new").disabled = operationsBlocked || busy || autosaving || state.settingsDirty;
    var connectionValidation = settingsValidationMessage(true);
    el("btn-settings-connect").disabled = operationsBlocked || busy || !writable || Boolean(connectionValidation);
    el("btn-settings-discover").disabled = el("btn-settings-connect").disabled;
    var missingSavedKey = currentSettingsProvider() === "api" && !(state.settings && state.settings.api && state.settings.api.api_key_configured);
    el("btn-settings-connect").textContent = missingSavedKey ? "保存密钥并获取模型" : "连接并获取模型";
    el("btn-settings-save").disabled = operationsBlocked || busy || !writable || !secretDirty
        || (!el("settings-clear-key").checked && !el("settings-api-key").value.trim());
    var retry = el("btn-settings-retry");
    if (retry) { retry.hidden = !ordinaryError; retry.disabled = operationsBlocked || busy || autosaving; }
    var noEnabledModels = !currentModelCatalog().some(function (item) { return item.enabled; });
    el("btn-test-enabled-models").disabled = operationsBlocked || busy || noEnabledModels;
    el("btn-test-enabled-nsfw").disabled = operationsBlocked || busy || noEnabledModels;
    all("[data-test-model], [data-test-model-nsfw]", el("settings-model-list")).forEach(function (button) {
        button.disabled = operationsBlocked || busy;
    });
    el("settings-key-state").textContent = secretDirty ? "待确认保存" : missingSavedKey ? "未配置" : "已配置";
    el("settings-save-note").textContent = active
        ? "任务进行中" : busy ? "处理中…" : saveError || (autosaving ? "保存中…"
            : secretDirty ? "密钥待确认" : state.settingsDirty ? "待保存" : "已保存");
}

function markSettingsDirty(connectionChanged, immediate) {
    state.settingsDirty = true;
    state.settingsConnectionDirty = state.settingsConnectionDirty || Boolean(connectionChanged);
    if (typeof scheduleSettingsAutosave === "function") scheduleSettingsAutosave(Boolean(immediate));
    updateSettingsControls(); renderModelBenchmark();
}
function setSettingsBusy(mode) {
    state.settingsBusy = mode || "";
    if (el("settings-fieldset")) { updateSettingsControls(); renderModelBenchmark(); }
}

function settingsPayload(includeSecret) {
    var payload = {
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
        api_protocol_mode: "auto",
        api_model_endpoints: Object.fromEntries(state.modelCatalog.api.filter(function (item) { return (item.supported_endpoint_types || []).length; })
            .map(function (item) { return [item.name, item.supported_endpoint_types.slice()]; })),
        disabled_ollama_models: state.modelCatalog.ollama.filter(function (item) {
            return !item.enabled;
        }).map(function (item) { return item.name; }),
        default_model: el("settings-default-model").value.trim() || currentSettingsProvider() + ":",
        disable_thinking: el("settings-disable-thinking").checked,
        ollama_host: el("settings-ollama-host").value.trim(),
        api_key_action: "keep",
    };
    if (includeSecret) {
        payload.api_key_action = el("settings-clear-key").checked ? "clear"
            : el("settings-api-key").value.trim() ? "replace" : "keep";
        if (payload.api_key_action === "replace") payload.api_key = el("settings-api-key").value.trim();
    }
    return payload;
}

async function saveSettings(event) {
    event.preventDefault();
    if (state.settingsBusy || settingsTaskIsActive() || benchmarkIsActive()) return false;
    if (typeof flushSettingsAutosave === "function" && !await flushSettingsAutosave()) return false;
    var secretEdited = Boolean(el("settings-api-key").value.trim() || el("settings-clear-key").checked);
    if (!secretEdited) return true;
    state.settingsSecretError = "";
    setSettingsBusy("saving");
    try {
        var result = await API.put("/settings", settingsPayload(true));
        state.settings = result.settings;
        state.settingsApiProfileId = state.settings.active_api_profile || "";
        state.settingsDirty = false;
        state.settingsConnectionDirty = false;
        if (typeof resetSettingsAutosave === "function") resetSettingsAutosave();
        loadPersistedModelStatuses();
        initializeModelCatalog();
        state.modelBenchmark = null;
        await loadModels(false);
        await loadModelBenchmark();
        renderSettings();
        invalidatePreflight();
        toast("密钥修改已保存", "success");
        return true;
    } catch (error) {
        state.settingsSecretError = "密钥尚未保存：" + error.message;
        toast(error.message, "error");
        return false;
    } finally {
        setSettingsBusy("");
    }
}

async function connectSettingsProvider() {
    if (state.settingsBusy || settingsTaskIsActive() || benchmarkIsActive()) return;
    var validation = settingsValidationMessage(true);
    if (validation) { toast(validation, "error"); return; }
    if (typeof flushSettingsAutosave === "function" && !await flushSettingsAutosave()) return;
    var provider = currentSettingsProvider();
    if (provider === "api" && !(state.settings.api || {}).api_key_configured) {
        if (!await saveSettings({ preventDefault: function () {} })) return;
    }
    setSettingsBusy("connecting");
    var discovered = false;
    try {
        var previousDefault = el("settings-default-model").value;
        var result = await API.post("/settings/models/discover", { provider: provider });
        var previous = new Map(currentModelCatalog().map(function (item) { return [item.id, item]; }));
        state.modelCatalog[provider] = (result.models || []).map(function (item) {
            var old = previous.get(item.id);
            return modelCatalogEntry(provider, item.name, old ? old.enabled : true, "",
                item.supported_endpoint_types || (old && old.supported_endpoint_types) || []);
        });
        renderSettingsModelPicker();
        if (currentModelCatalog().some(function (item) { return item.id === previousDefault && item.enabled; })) {
            el("settings-default-model").value = previousDefault;
        } else {
            var first = currentModelCatalog().find(function (item) { return item.enabled; });
            el("settings-default-model").value = first ? first.id : "";
        }
        discovered = true;
        el("settings-connect-note").textContent = result.ok
            ? "已获取 " + result.models.length + " 个模型"
            : "模型目录暂不可用";
        toast(result.ok ? "模型目录已更新" : result.warning || "模型目录读取失败", result.ok ? "success" : "error");
    } catch (error) {
        toast(error.message, "error");
        el("settings-connect-note").textContent = "获取失败：" + error.message;
    } finally {
        setSettingsBusy("");
    }
    if (discovered) {
        markSettingsDirty(false, true);
        if (typeof flushSettingsAutosave === "function") await flushSettingsAutosave();
    }
}

function setAllCurrentModels(enabled) {
    currentModelCatalog().forEach(function (item) { item.enabled = enabled; });
    renderSettingsModelPicker();
    markSettingsDirty(false, true);
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
    markSettingsDirty(false, true);
}

function bindSettingsEvents() {
    bindModelBenchmarkEvents();
    el("settings-form").addEventListener("submit", saveSettings);
    if (el("btn-settings-retry")) el("btn-settings-retry").addEventListener("click", function () { flushSettingsAutosave(); });
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
        var checkbox = event.target.closest("[data-model-enabled]");
        if (!checkbox) return;
        var item = currentModelCatalog().find(function (entry) {
            return entry.id === checkbox.dataset.modelEnabled;
        });
        if (item) item.enabled = checkbox.checked;
        renderModelPickerMetadata();
        markSettingsDirty(false, true);
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
        markSettingsDirty(false, true);
    });
    el("settings-default-model").addEventListener("change", function () { markSettingsDirty(false, true); });
    ["settings-base-url", "settings-ollama-host"].forEach(function (id) {
        el(id).addEventListener("input", function () {
            markSettingsDirty(true);
        });
    });
    el("settings-disable-thinking").addEventListener("change", function () { markSettingsDirty(true, true); });
    el("settings-api-key").addEventListener("input", function () {
        if (this.value) el("settings-clear-key").checked = false;
        state.settingsSecretError = "";
        if (typeof syncSettingsDirtyState === "function") syncSettingsDirtyState();
        else state.settingsDirty = true;
        updateSettingsControls();
    });
    el("settings-clear-key").addEventListener("change", function () {
        if (this.checked) el("settings-api-key").value = "";
        state.settingsSecretError = "";
        if (typeof syncSettingsDirtyState === "function") syncSettingsDirtyState();
        else state.settingsDirty = true;
        updateSettingsControls();
    });
}
