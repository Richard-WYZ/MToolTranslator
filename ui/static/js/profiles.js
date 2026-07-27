"use strict";

async function loadModels(showNotice) {
    try {
        var payload = await API.get("/models");
        state.models = payload.models || [];
        state.runtime = payload.runtime || {};
        configureProfiles();
        populateModelSelectors();
        renderProfile();
        if (state.settings && !state.settingsDirty) renderSettings();
        if (showNotice) toast("模型与提供方状态已刷新", "success");
    } catch (error) {
        toast(error.message, "error");
    }
}

function configureProfiles() {
    var profiles = (state.runtime && state.runtime.profiles) || [];
    profiles.forEach(function (profile) {
        var card = document.querySelector('[data-profile-card="' + profile.id + '"]');
        if (!card) return;
        card.classList.toggle("unavailable", !profile.available);
        card.querySelector("input").disabled = !profile.available;
    });
    var ollama = state.runtime && state.runtime.providers && state.runtime.providers.ollama;
    el("local-profile-note").textContent = ollama && ollama.configured
        ? ollama.model_count + " 个本地模型可用"
        : "Ollama 未连接或没有模型";

    var saved = localStorage.getItem("lgt.executionProfile");
    var candidate = profiles.find(function (profile) { return profile.id === saved && profile.available; });
    if (!candidate) candidate = profiles.find(function (profile) { return profile.id === "quality_first" && profile.available; });
    if (!candidate) candidate = profiles.find(function (profile) { return profile.available; });
    if (candidate) state.profile = candidate.id;
    all('input[name="execution-profile"]').forEach(function (input) { input.checked = input.value === state.profile; });
}

function modelsForProvider(provider) {
    return state.models.filter(function (model) { return model.provider === provider; });
}

function fillModelSelect(select, models, selected, allowEmpty) {
    select.innerHTML = "";
    if (allowEmpty) {
        var empty = document.createElement("option");
        empty.value = "";
        empty.textContent = "不启用";
        select.appendChild(empty);
    }
    if (!models.length && !allowEmpty) {
        var unavailable = document.createElement("option");
        unavailable.value = "";
        unavailable.textContent = "没有可用模型";
        select.appendChild(unavailable);
        return;
    }
    var groups = {};
    models.forEach(function (model) {
        var provider = model.provider || "other";
        if (!groups[provider]) {
            groups[provider] = document.createElement("optgroup");
            groups[provider].label = provider === "api" ? "联网 API（配置列表）" : "本地 Ollama（实时）";
            select.appendChild(groups[provider]);
        }
        var option = document.createElement("option");
        option.value = model.id || model.name;
        option.textContent = model.display_name || modelLabel(model.name);
        groups[provider].appendChild(option);
    });
    var values = models.map(function (model) { return model.id || model.name; });
    if (selected && (values.includes(selected) || (allowEmpty && selected === ""))) select.value = selected;
}

function populateModelSelectors() {
    var primary = el("model-select");
    var apiModels = modelsForProvider("api");
    var localModels = modelsForProvider("ollama");
    var current = primary.value || localStorage.getItem("lgt.selectedModel") || (state.runtime && state.runtime.default_model) || "";
    var visible = state.profile === "local" ? localModels : apiModels;
    var visibleIds = visible.map(function (model) { return model.id || model.name; });
    if (!visibleIds.includes(current)) {
        var configured = state.runtime && state.runtime.default_model;
        current = visibleIds.includes(configured)
            ? configured
            : visibleIds.includes("api:qwen3.7-plus")
                ? "api:qwen3.7-plus"
                : visibleIds[0] || "";
    }
    fillModelSelect(primary, visible, current, false);
    ["fast-model", "sensitive-model", "quality-model"].forEach(function (id) {
        var target = id === "quality-model" ? "api:qwen3.7-plus" : "api:minimax-m3";
        fillModelSelect(el(id), apiModels, el(id).value || target, true);
    });
}

function selectedModel() {
    if (state.profile === "quality_first") return "api:qwen3.7-plus";
    return el("model-select").value;
}

function profileOptions() {
    if (state.profile !== "custom") return {};
    return {
        protocol: el("custom-protocol").value,
        json_batch_size: Number(el("custom-batch-size").value),
        max_batch_chars: Number(el("custom-max-chars").value),
        api_concurrency: Number(el("custom-concurrency").value),
        api_parallel_enabled: true,
        api_event_driven_enabled: true,
        api_adaptive_concurrency_enabled: true,
        api_model_routing_enabled: Boolean(el("fast-model").value || el("quality-model").value),
        api_fast_model: el("fast-model").value,
        api_quality_model: el("quality-model").value,
        api_sensitive_routing_enabled: Boolean(el("sensitive-model").value),
        api_sensitive_model: el("sensitive-model").value,
    };
}

function renderProfile() {
    var protocolSelect = el("custom-protocol");
    protocolSelect.querySelector('option[value="json"]').textContent = "JSON（推荐）";
    protocolSelect.querySelector('option[value="line"]').textContent = "Line（实验性）";
    if (!protocolSelect.dataset.defaultApplied) {
        protocolSelect.value = "json";
        protocolSelect.dataset.defaultApplied = "true";
    }
    all(".profile-card").forEach(function (card) {
        card.classList.toggle("selected", card.dataset.profileCard === state.profile);
    });
    localStorage.setItem("lgt.executionProfile", state.profile);
    populateModelSelectors();
    var primary = el("model-select");
    var quality = state.profile === "quality_first";
    primary.disabled = quality;
    if (quality) {
        fillModelSelect(primary, [{ id: "api:qwen3.7-plus", name: "api:qwen3.7-plus", provider: "api", display_name: "qwen3.7-plus（固定主模型）" }], "api:qwen3.7-plus", false);
    }
    el("advanced-config").style.display = state.profile === "custom" ? "block" : "none";
    if (state.profile === "custom") el("advanced-config").open = true;
    var descriptions = {
        quality_first: "质量优先方案使用固定的已验证路由。",
        single_model: "整个任务只使用这个 API 模型。",
        local: "整个任务只使用这个本地 Ollama 模型。",
        custom: "此模型用于普通文本；其他模型在高级配置中选择。",
    };
    el("model-help").textContent = descriptions[state.profile] || "";
    var profiles = (state.runtime && state.runtime.profiles) || [];
    var profile = profiles.find(function (item) { return item.id === state.profile; });
    el("profile-step-state").textContent = profile ? profile.name : state.profile;
    renderRouteMap();
    invalidatePreflight();
}

function effectiveRoutes() {
    var primary = selectedModel();
    if (state.profile === "quality_first") {
        return [
            { role: "短标签", model: "api:minimax-m3" },
            { role: "普通文本", model: "api:qwen3.7-plus" },
            { role: "敏感文本", model: "api:minimax-m3" },
            { role: "质量修复", model: "api:qwen3.7-plus" },
        ];
    }
    if (state.profile === "custom") {
        var options = profileOptions();
        var routes = [{ role: "普通文本", model: primary }];
        if (options.api_fast_model) routes.unshift({ role: "短标签", model: options.api_fast_model });
        if (options.api_sensitive_model) routes.push({ role: "敏感文本", model: options.api_sensitive_model });
        if (options.api_quality_model) routes.push({ role: "质量修复", model: options.api_quality_model });
        return routes;
    }
    return [{ role: "全部文本", model: primary }];
}

function renderRouteMap() {
    var routes = effectiveRoutes();
    el("route-map").innerHTML = routes.map(function (route) {
        return '<span class="route-chip"><b>' + escapeHtml(route.role) + '</b><span>' + escapeHtml(modelLabel(route.model) || "未选择") + "</span></span>";
    }).join("");
    var profileName = { quality_first: "联网 · 质量优先", single_model: "联网 · 单模型", local: "本地 · Ollama", custom: "高级 · 自定义路由" }[state.profile];
    var isLocal = state.profile === "local";
    var privacy = isLocal ? "local" : "online";
    el("privacy-badge").className = "privacy-badge " + privacy;
    el("privacy-badge").textContent = privacy === "local" ? "仅本机" : privacy === "hybrid" ? "混合" : "联网";
    el("privacy-copy").textContent = privacy === "local"
        ? "文本只发送到本机 Ollama 服务。"
        : "文本会通过已配置的 OpenCode Go API 发送给路由模型。";
    el("summary-profile").textContent = profileName;
    el("summary-model").textContent = modelLabel(selectedModel()) || "未选择";
    var options = profileOptions();
    var concurrency = state.profile === "quality_first" ? 10 : state.profile === "custom" ? options.api_concurrency : state.profile === "local" ? 1 : 10;
    var batch = state.profile === "quality_first" ? 40 : state.profile === "custom" ? options.json_batch_size : 40;
    el("summary-batch").textContent = concurrency + " / " + batch;
    el("top-provider").textContent = privacy === "local" ? "Ollama · 本地" : privacy === "hybrid" ? "本地 + API" : "OpenCode Go · 联网";
    el("btn-start").textContent = state.profile === "quality_first" ? "开始质量优先翻译" : "开始翻译";
}

function invalidatePreflight() {
    state.preflight = null;
    el("preflight-empty").hidden = false;
    el("preflight-result").hidden = true;
    updateActionStates();
    renderRouteMap();
}
