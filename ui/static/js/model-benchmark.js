"use strict";

function benchmarkIsActive() {
    return ["starting", "running", "stopping"].includes((state.modelBenchmark || {}).status);
}

async function loadModelBenchmark() {
    try {
        state.modelBenchmark = await API.get("/settings/benchmark");
        renderModelBenchmark();
        if (benchmarkIsActive()) scheduleBenchmarkPoll();
    } catch (error) {
        if (el("benchmark-empty")) el("benchmark-empty").textContent = error.message;
    }
}

function scheduleBenchmarkPoll() {
    clearTimeout(state.benchmarkPollingTimer);
    if (!benchmarkIsActive()) return;
    state.benchmarkPollingTimer = setTimeout(loadModelBenchmark, 700);
}

function renderModelBenchmark() {
    if (!el("model-benchmark-panel")) return;
    var status = state.modelBenchmark || { status: "idle" };
    var active = benchmarkIsActive();
    var result = status.result;
    var progress = el("benchmark-progress");
    progress.hidden = !active;
    el("btn-benchmark-start").disabled = active || Boolean(state.settingsBusy) || state.settingsDirty
        || ["running", "paused", "stopping"].includes(state.taskStatus)
        || !currentModelCatalog().some(function (item) { return item.enabled; });
    el("btn-benchmark-cancel").hidden = !active;
    el("benchmark-mode").disabled = active;
    if (active) {
        var percentage = Number(status.percentage || 0);
        el("benchmark-progress-value").textContent = percentage.toFixed(1) + "%";
        el("benchmark-progress-bar").style.width = percentage + "%";
        el("benchmark-progress-label").textContent = status.status === "stopping"
            ? "正在停止（当前请求结束后生效）"
            : (status.current_model ? modelLabel(status.current_model) + " · " + (status.current_case || "准备中") : "准备中");
    }
    el("benchmark-empty").hidden = Boolean(result);
    el("benchmark-results").hidden = !result;
    if (result) el("benchmark-results").innerHTML = renderBenchmarkResult(result, status.applied);
    if (status.status === "error" && status.error) {
        el("benchmark-empty").hidden = false;
        el("benchmark-empty").textContent = "Benchmark 失败：" + status.error;
    }
}

function renderBenchmarkResult(result, applied) {
    var rows = (result.results || []).map(function (item) {
        return '<tr><td><strong>' + escapeHtml(modelLabel(item.model)) + '</strong><small>'
            + escapeHtml(protocolLabel(item.protocol)) + '</small></td><td>'
            + Number(item.quality_score || 0).toFixed(1) + '</td><td>'
            + Number(item.speed_score || 0).toFixed(1) + '</td><td>'
            + Number(item.balanced_score || 0).toFixed(1) + '</td><td>'
            + benchmarkReliability(item) + '</td><td>'
            + (item.nsfw_supported ? Number(item.nsfw_score || 0).toFixed(1) : "不通过") + '</td></tr>';
    }).join("");
    var recommendations = result.recommendations || {};
    var strategies = [
        ["quality", "最佳效果", "质量最高，速度作为同分条件"],
        ["efficiency", "最佳效率", "质量合格范围内优先速度"],
        ["balanced", "综合最佳", "质量 65% + 速度 35%"],
        ["nsfw", "成人内容", "综合主路由 + 独立 NSFW 主备模型"],
    ];
    var cards = strategies.map(function (entry) {
        var profile = (recommendations.profiles || {})[entry[0]] || {};
        var selected = applied && applied.strategy === entry[0];
        return '<article class="benchmark-card' + (selected ? " selected" : "") + '"><div><strong>'
            + entry[1] + (selected ? " · 已应用" : "") + '</strong><span>' + entry[2] + '</span></div>'
            + '<p>主模型：' + escapeHtml(modelLabel(profile.primary_model || "—"))
            + '<br>快速：' + escapeHtml(modelLabel(profile.fast_model || "—"))
            + '<br>质量修复：' + escapeHtml(modelLabel(profile.quality_model || "—"))
            + '<br>NSFW：' + escapeHtml(modelLabel(profile.sensitive_model || "—"))
            + ' → ' + escapeHtml(modelLabel(profile.sensitive_fallback_model || "—")) + '</p>'
            + '<button type="button" class="btn btn-secondary btn-sm" data-apply-benchmark="'
            + entry[0] + '"' + (result.stale || selected || recommendations.auto_applicable === false ? " disabled" : "") + '>应用此方案</button></article>';
    }).join("");
    var warning = result.stale ? "连接、密钥、协议或模型列表已变化；请重新测试后再应用。"
        : recommendations.warning || "可靠性是硬门槛；不合格模型不会参与正常推荐。";
    return '<div class="benchmark-meta"><span>' + escapeHtml(result.mode || "standard")
        + ' · ' + (result.results || []).length + ' 个模型 · ' + formatDuration(result.elapsed_seconds)
        + '</span><span>' + escapeHtml(warning) + '</span></div><div class="benchmark-table-wrap">'
        + '<table class="benchmark-table"><thead><tr><th>模型</th><th>质量</th><th>速度</th><th>综合</th><th>可靠性</th><th>NSFW</th></tr></thead><tbody>'
        + rows + '</tbody></table></div><div class="benchmark-cards">' + cards + '</div>';
}

function protocolLabel(protocol) {
    return { responses: "Responses", messages: "Messages", chat_completions: "Chat", ollama: "Ollama" }[protocol] || protocol || "自动";
}

function benchmarkReliability(item) {
    var rate = Math.round(Number(item.success_rate || 0) * 100);
    return '<span class="health-badge ' + (item.qualified ? "good" : "bad") + '">'
        + rate + '% · ' + Number(item.critical_failures || 0) + ' 严重</span>';
}

async function startModelBenchmark() {
    if (state.settingsDirty) {
        toast("请先保存模型与协议设置", "error");
        return;
    }
    var models = currentModelCatalog().filter(function (item) { return item.enabled; }).map(function (item) { return item.id; });
    var mode = el("benchmark-mode").value;
    var calls = models.length * ({ quick: 6, standard: 10, deep: 30 }[mode] || 10);
    if (!window.confirm("将对 " + models.length + " 个模型发送约 " + calls + " 次短请求，可能产生 Token 用量。继续吗？")) return;
    try {
        state.modelBenchmark = await API.post("/settings/benchmark/start", {
            provider: currentSettingsProvider(), models: models, mode: mode,
        });
        renderModelBenchmark();
        scheduleBenchmarkPoll();
    } catch (error) {
        toast(error.message, "error");
    }
}

async function cancelModelBenchmark() {
    try {
        state.modelBenchmark = await API.post("/settings/benchmark/cancel", {});
        renderModelBenchmark();
        scheduleBenchmarkPoll();
    } catch (error) {
        toast(error.message, "error");
    }
}

async function applyModelBenchmark(strategy) {
    try {
        var response = await API.post("/settings/benchmark/apply", { strategy: strategy });
        state.settings = response.settings;
        state.settingsDirty = false;
        state.settingsConnectionDirty = false;
        initializeModelCatalog();
        await Promise.all([loadModels(false), loadModelBenchmark()]);
        renderSettings();
        toast("已应用 " + strategy + " benchmark 路由", "success");
    } catch (error) {
        toast(error.message, "error");
    }
}

function bindModelBenchmarkEvents() {
    el("btn-benchmark-start").addEventListener("click", startModelBenchmark);
    el("btn-benchmark-cancel").addEventListener("click", cancelModelBenchmark);
    el("benchmark-results").addEventListener("click", function (event) {
        var button = event.target.closest("[data-apply-benchmark]");
        if (button) applyModelBenchmark(button.dataset.applyBenchmark);
    });
}
