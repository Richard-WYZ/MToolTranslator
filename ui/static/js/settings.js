"use strict";

async function loadSettings(showNotice) {
    setSettingsBusy("loading");
    try {
        state.settings = await API.get("/settings");
        state.settingsDirty = false;
        state.settingsConnectionDirty = false;
        loadPersistedModelStatuses();
        initializeModelCatalog();
        renderSettings();
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
    var localDisabled = new Set(ollama.disabled_models || []);
    state.modelCatalog.api = (api.models || []).map(function (name) {
        return modelCatalogEntry("api", name, !apiDisabled.has(name));
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
    if (parts.length === 2 && state.modelCatalog[parts[0]]
        && !state.modelCatalog[parts[0]].some(function (item) { return item.id === configured; })) {
        state.modelCatalog[parts[0]].push(modelCatalogEntry(
            parts[0],
            parts[1],
            !(parts[0] === "api" ? apiDisabled : localDisabled).has(parts[1])
        ));
    }
}

function modelCatalogEntry(provider, name, enabled) {
    var clean = String(name || "").replace(/^(api|ollama):/, "").trim();
    return {
        id: provider + ":" + clean,
        name: clean,
        provider: provider,
        enabled: enabled !== false,
    };
}

function renderSettings() {
    if (!state.settings) return;
    var settings = state.settings;
    var api = settings.api || {};
    var ollama = settings.ollama || {};
    var file = settings.file || {};
    el("settings-provider").value = settings.provider || "api";
    el("settings-api-style").value = api.style || "opencode_go";
    el("settings-base-url").value = api.base_url || "";
    el("settings-default-model").value = settings.default_model || "";
    el("settings-ollama-host").value = ollama.host || "http://localhost:11434";
    el("settings-disable-thinking").checked = api.disable_thinking !== false;
    el("settings-api-key").value = "";
    el("settings-clear-key").checked = false;
    el("settings-key-state").textContent = api.api_key_configured
        ? "已配置（来源：" + keySourceLabel(api.api_key_source) + "）"
        : "尚未配置";
    el("settings-file-state").className = "settings-file-state" + (file.writable ? "" : " error");
    el("settings-file-state").textContent = "配置文件：" + (file.path || "未知")
        + " · " + (file.exists ? "已存在" : "保存时创建")
        + " · " + (file.writable ? "可写" : "不可写")
        + " · " + (settings.precedence_note || "");
    renderSettingsModelPicker();
    renderSettingsStatus();
    updateSettingsControls();
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
        : '<div class="model-empty">当前没有模型。保存连接配置后点击“获取模型”。</div>';
}

function renderModelPickerMetadata() {
    var provider = currentSettingsProvider();
    var catalog = currentModelCatalog();
    var enabled = catalog.filter(function (item) { return item.enabled; });
    el("settings-key-field").hidden = provider !== "api";
    el("settings-model-summary").textContent = enabled.length + " / " + catalog.length
        + " 个模型已启用 · " + (provider === "api" ? "联网 API" : "本地 Ollama");
    el("settings-model-options").innerHTML = enabled.map(function (item) {
        return '<option value="' + escapeHtml(item.id) + '"></option>';
    }).join("");
}

function renderModelRow(item) {
    var basicRecord = modelTestRecord(state.modelAvailability, item.id);
    var nsfwRecord = modelTestRecord(state.modelNsfwAvailability, item.id);
    var availability = basicRecord.status;
    var nsfwAvailability = nsfwRecord.status;
    var testDisabled = state.settingsConnectionDirty || Boolean(state.settingsBusy)
        || ["running", "paused", "stopping"].includes(state.taskStatus);
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
    if (!state.runtime) return;
    var providers = state.runtime.providers || {};
    var providerRows = [
        {
            name: "联网 API",
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

function settingsValidationMessage() {
    if (!state.settings) return "设置尚未加载";
    var provider = currentSettingsProvider();
    var defaultModel = el("settings-default-model").value.trim();
    if (!defaultModel.startsWith(provider + ":")) return "默认模型必须匹配当前提供方";
    var selected = currentModelCatalog().find(function (item) {
        return item.id === defaultModel && item.enabled;
    });
    if (!selected) return "默认模型必须在上方列表中保持勾选";
    if (provider === "api") {
        var hasKey = (state.settings.api || {}).api_key_configured
            || Boolean(el("settings-api-key").value.trim());
        if (!hasKey || el("settings-clear-key").checked) return "联网 API 需要有效密钥";
        if (el("settings-api-style").value !== "opencode_go"
            && !el("settings-base-url").value.trim()) return "兼容 API 需要 Base URL";
    }
    return "";
}

function updateSettingsControls() {
    var active = ["running", "paused", "stopping"].includes(state.taskStatus);
    var busy = Boolean(state.settingsBusy);
    var writable = !state.settings || !state.settings.file || state.settings.file.writable;
    var validation = settingsValidationMessage();
    el("settings-fieldset").disabled = active || busy;
    el("settings-dirty-badge").className = "status-badge "
        + (busy ? "running" : state.settingsDirty ? "warning" : "neutral");
    el("settings-dirty-badge").textContent = busy
        ? { loading: "正在加载", saving: "正在保存", discovering: "正在获取模型", testing: "正在测试模型" }[state.settingsBusy]
        : state.settingsDirty ? "有未保存修改" : "尚未修改";
    el("btn-settings-refresh").disabled = busy;
    el("btn-settings-save").disabled = active || busy || !writable || !state.settingsDirty || Boolean(validation);
    el("btn-settings-discover").disabled = active || busy || state.settingsConnectionDirty
        || (currentSettingsProvider() === "api"
            && !(state.settings && state.settings.api && state.settings.api.api_key_configured));
    var noEnabledModels = !currentModelCatalog().some(function (item) { return item.enabled; });
    el("btn-test-enabled-models").disabled = active || busy || state.settingsConnectionDirty || noEnabledModels;
    el("btn-test-enabled-nsfw").disabled = active || busy || state.settingsConnectionDirty || noEnabledModels;
    all("[data-test-model], [data-test-model-nsfw]", el("settings-model-list")).forEach(function (button) {
        button.disabled = active || busy || state.settingsConnectionDirty;
    });
    el("settings-save-note").textContent = active
        ? "翻译任务活动期间不能修改、获取或测试模型。"
        : busy ? "请等待当前设置操作完成。"
        : !writable ? "配置目录不可写；请把软件移动到可写目录。"
        : validation || (state.settingsConnectionDirty
            ? "连接参数已修改：请先保存，再获取或测试模型。"
            : "模型可用性测试可能消耗少量 Token；不会自动执行。");
}

function markSettingsDirty(connectionChanged) {
    state.settingsDirty = true;
    state.settingsConnectionDirty = state.settingsConnectionDirty || Boolean(connectionChanged);
    updateSettingsControls();
}

function setSettingsBusy(mode) {
    state.settingsBusy = mode || "";
    if (el("settings-fieldset")) updateSettingsControls();
}

function settingsPayload() {
    return {
        provider: currentSettingsProvider(),
        api_style: el("settings-api-style").value,
        api_base_url: el("settings-base-url").value.trim(),
        api_models: state.modelCatalog.api.map(function (item) { return item.name; }),
        disabled_api_models: state.modelCatalog.api.filter(function (item) {
            return !item.enabled;
        }).map(function (item) { return item.name; }),
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
        state.settingsDirty = false;
        state.settingsConnectionDirty = false;
        loadPersistedModelStatuses();
        initializeModelCatalog();
        await loadModels(false);
        renderSettings();
        toast("设置已保存；翻译页只会显示已勾选模型", "success");
    } catch (error) {
        toast(error.message, "error");
    } finally {
        setSettingsBusy("");
    }
}

async function discoverSettingsModels() {
    if (state.settingsConnectionDirty) {
        toast("请先保存连接参数，再获取模型", "error");
        return;
    }
    var provider = currentSettingsProvider();
    var existing = new Map(currentModelCatalog().map(function (item) {
        return [item.id, item.enabled];
    }));
    setSettingsBusy("discovering");
    try {
        var result = await API.post("/settings/models/discover", { provider: provider });
        state.modelCatalog[provider] = (result.models || []).map(function (item) {
            return modelCatalogEntry(provider, item.name, existing.has(item.id) ? existing.get(item.id) : true);
        });
        if (provider === "api") markSettingsDirty(false);
        renderSettingsModelPicker();
        if (result.ok) toast("已获取 " + result.models.length + " 个模型；未发送推理请求", "success");
        else toast(result.warning || "实时获取失败，已保留配置目录", "error");
    } catch (error) {
        toast(error.message, "error");
    } finally {
        setSettingsBusy("");
    }
}

function setAllCurrentModels(enabled) {
    currentModelCatalog().forEach(function (item) { item.enabled = enabled; });
    renderSettingsModelPicker();
    markSettingsDirty(false);
}

function bindSettingsEvents() {
    el("settings-form").addEventListener("submit", saveSettings);
    el("btn-settings-refresh").addEventListener("click", function () { loadSettings(true); });
    el("btn-settings-discover").addEventListener("click", discoverSettingsModels);
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
    el("settings-default-model").addEventListener("input", function () { markSettingsDirty(false); });
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
