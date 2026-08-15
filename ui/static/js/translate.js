"use strict";

async function ensureCanReplaceFile() {
    if (!state.filePath) return true;
    if (["running", "paused", "stopping", "finalizing"].includes(state.taskStatus)) {
        toast("任务仍在运行，请先停止并等待写盘", "error");
        return false;
    }
    var dirty = state.hasUnexportedResult;
    try {
        var result = await API.get("/translation/dirty-state?file_path=" + encodeURIComponent(state.filePath));
        dirty = dirty || result.dirty;
    } catch (error) {
        // 本地状态仍足够用于确认。
    }
    if (!dirty) return true;
    if (!window.confirm("当前任务有未导出的翻译或断点。切换文件会清理该临时结果，是否继续？")) return false;
    await API.post("/translation/cleanup", { file_path: state.filePath, task_id: state.taskId || null });
    return true;
}

async function applyImportedFile(result, fallbackSize) {
    state.filePath = result.saved_path; state.sourceFilePath = result.saved_path;
    state.originalFilePath = result.original_path || ""; state.fileName = result.filename;
    state.fileSize = result.file_size || fallbackSize || 0; state.sessionId = result.session_id;
    state.taskId = ""; state.taskStatus = "idle";
    state.hasUnexportedResult = false; state.exportReady = false;
    await loadPreview(); renderFile(); invalidatePreflight();
    toast("文件已导入，尚未发送给任何模型", "success");
}

async function chooseSourceFile() {
    var bridge = window.pywebview && window.pywebview.api;
    if (!bridge || typeof bridge.choose_source_file !== "function") {
        el("file-input").click();
        return;
    }
    if (!(await ensureCanReplaceFile())) return;
    lockFileControls(true); try {
        var selected = await bridge.choose_source_file();
        if (!selected || !selected.token) return;
        if (!String(selected.filename || "").toLowerCase().endsWith(".json")) {
            toast("只支持 MTool JSON 文件", "error");
            return;
        }
        var result = await API.importLocal(selected.token);
        await applyImportedFile(result, selected.size);
    } catch (error) {
        toast(error.message, "error");
    } finally {
        lockFileControls(false);
        el("file-input").value = "";
    }
}

async function handleFile(file, sourceToken) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".json")) {
        toast("只支持 MTool JSON 文件", "error");
        return;
    }
    if (!(await ensureCanReplaceFile())) return;
    lockFileControls(true);
    try {
        var result = await API.upload(file, sourceToken || "");
        await applyImportedFile(result, file.size);
    } catch (error) {
        toast(error.message, "error");
    } finally {
        lockFileControls(false);
        el("file-input").value = "";
    }
}

async function loadPreview() {
    if (!state.filePath) return;
    state.preview = await API.get("/preview?path=" + encodeURIComponent(state.filePath) + "&limit=10");
    state.totalRows = state.preview.total_rows || 0;
}

function renderFile() {
    var hasFile = Boolean(state.filePath);
    el("upload-zone").hidden = hasFile;
    el("file-card").hidden = !hasFile;
    el("file-step-state").textContent = hasFile ? "已导入" : "等待文件";
    if (!hasFile) {
        el("summary-file").textContent = "尚未选择";
        el("btn-preflight").disabled = true;
        return;
    }
    el("file-name").textContent = state.fileName;
    el("file-size").textContent = formatSize(state.fileSize);
    el("file-entry-count").textContent = state.totalRows.toLocaleString("zh-CN");
    el("summary-file").textContent = state.fileName;
    el("btn-preflight").disabled = false;
    var rows = (state.preview && state.preview.rows) || [];
    el("preview-body").innerHTML = rows.map(function (row, index) {
        return "<tr><td>" + (index + 1) + "</td><td title=\"" + escapeHtml(row[0]) + "\">" + escapeHtml(row[0]) + "</td><td title=\"" + escapeHtml(row[1]) + "\">" + escapeHtml(row[1]) + "</td></tr>";
    }).join("");
}

function resetFile() {
    stopPolling();
    state.filePath = "";
    state.sourceFilePath = "";
    state.originalFilePath = "";
    state.fileName = "";
    state.fileSize = 0;
    state.sessionId = "";
    state.totalRows = 0;
    state.preview = null;
    state.preflight = null;
    state.taskId = "";
    state.taskStatus = "idle";
    state.hasUnexportedResult = false;
    state.exportReady = false;
    state.review = {
        stats: null, filter: "required", offset: 0, limit: 30, total: 0,
        items: [], selectedRow: null, selectedRows: new Set(), selectedItems: new Map(), selectionFilePath: "",
        loadRequestId: 0, listRequestId: 0, listController: null,
        aiTaskId: "", aiTaskStatus: "idle", aiPollingTimer: null,
        aiPollingGeneration: 0, aiProgress: null,
    };
    el("preview-wrap").hidden = true;
    el("result-summary").hidden = true;
    resetTaskDisplay();
    renderFile();
    invalidatePreflight();
}

function lockFileControls(locked) {
    el("upload-zone").classList.toggle("disabled", locked);
    el("btn-remove-file").disabled = locked;
}

async function runPreflight() {
    if (!state.filePath) return;
    el("btn-preflight").disabled = true;
    el("btn-preflight").textContent = "正在分析…";
    try {
        var payload = await API.post("/preflight", {
            file_path: state.filePath,
            model: selectedModel(),
            provider: selectedModel().startsWith("ollama:") ? "ollama" : "api",
            execution_profile: state.profile,
            profile_options: profileOptions(),
        });
        state.preflight = payload;
        renderPreflight();
        toast(payload.ok ? "预检完成，可以启动翻译" : "预检发现阻塞问题", payload.ok ? "success" : "error");
    } catch (error) {
        state.preflight = null;
        toast(error.message, "error");
    } finally {
        el("btn-preflight").disabled = false;
        el("btn-preflight").textContent = "重新预检";
        updateActionStates();
    }
}

function renderPreflight() {
    var payload = state.preflight;
    if (!payload) return;
    el("preflight-empty").hidden = true;
    el("preflight-result").hidden = false;
    el("pf-total").textContent = payload.file.total_entries.toLocaleString("zh-CN");
    el("pf-model").textContent = payload.file.model_bound_entries.toLocaleString("zh-CN");
    el("pf-preserved").textContent = payload.file.deterministic_or_preserved_entries.toLocaleString("zh-CN");
    el("pf-checkpoint").textContent = payload.checkpoint.found
        ? payload.checkpoint.completed.toLocaleString("zh-CN") + (payload.checkpoint.model_match ? "" : " · 需重验")
        : "无";
    el("summary-checkpoint").textContent = payload.checkpoint.found
        ? (payload.checkpoint.model_match ? "待校验 " : "配置不同 · ") + payload.checkpoint.completed + " / " + payload.checkpoint.total
        : "新任务";
    el("preflight-routes").innerHTML = '<div class="route-map">' + payload.profile.routes.map(function (route) {
        return '<span class="route-chip"><b>' + escapeHtml(route.role) + "</b><span>" + escapeHtml(modelLabel(route.model)) + "</span></span>";
    }).join("") + "</div>";
    var notices = (payload.warnings || []).map(function (warning) {
        return '<div class="notice warning">' + escapeHtml(warning) + "</div>";
    });
    if (payload.ok) notices.unshift('<div class="notice success">文件结构、执行方案与提供方配置检查通过。</div>');
    el("preflight-warnings").innerHTML = notices.join("");
}

async function startTranslation() {
    if (!state.preflight || !state.preflight.ok || !state.filePath) return;
    updateTaskStatus("starting");
    try {
        var result = await API.post("/translate/start", {
            file_path: state.filePath,
            model: selectedModel(),
            provider: selectedModel().startsWith("ollama:") ? "ollama" : "api",
            prompt_style: "professional",
            execution_profile: state.profile,
            profile_options: profileOptions(),
        });
        state.taskId = result.task_id;
        state.taskStatus = "running";
        state.hasUnexportedResult = true;
        state.exportReady = false;
        el("result-summary").hidden = true;
        startPolling();
        updateActionStates();
        toast("翻译任务已启动", "success");
    } catch (error) {
        state.taskStatus = "error";
        updateTaskStatus("error", error.message);
        toast(error.message, "error");
    }
}

function startPolling() {
    stopPolling();
    state.pollingFailures = 0;
    var generation = ++state.pollingGeneration;
    scheduleProgressPoll(generation, 0);
}
function stopPolling() {
    if (state.pollingTimer) {
        clearTimeout(state.pollingTimer);
        state.pollingTimer = null;
    }
    state.pollingGeneration += 1;
}

function scheduleProgressPoll(generation, delay) {
    if (generation !== state.pollingGeneration || !state.taskId) return;
    state.pollingTimer = setTimeout(function () { pollProgress(generation); }, delay);
}

async function pollProgress(generation) {
    if (!state.taskId || generation !== state.pollingGeneration) return;
    try {
        var progress = await API.get("/translate/" + encodeURIComponent(state.taskId) + "/progress");
        if (generation !== state.pollingGeneration) return;
        state.pollingFailures = 0;
        state.taskStatus = progress.status;
        renderProgress(progress);
        if (["completed", "cancelled", "error"].includes(progress.status)) {
            stopPolling();
            state.hasUnexportedResult = Boolean(progress.snapshot_ready);
            state.exportReady = Boolean(progress.snapshot_ready);
            await refreshReviewCount();
            return;
        }
        updateActionStates(progress);
        scheduleProgressPoll(generation, 900);
    } catch (error) {
        if (generation !== state.pollingGeneration) return;
        state.pollingFailures += 1;
        if (state.pollingFailures >= 2) await refreshTaskStatusFallback(generation);
        if (state.pollingFailures === 3) toast("进度连接暂时中断，正在自动重试", "error");
        scheduleProgressPoll(generation, Math.min(5000, 900 * state.pollingFailures));
    }
}

async function refreshTaskStatusFallback(generation) {
    try {
        var payload = await API.get("/translate/tasks");
        if (generation !== state.pollingGeneration) return;
        var task = (payload.tasks || []).find(function (item) { return item.task_id === state.taskId; });
        if (!task) return;
        state.taskStatus = task.status;
        updateTaskStatus(task.status);
    } catch (error) {
        // The sequential progress loop remains authoritative and keeps retrying.
    }
}

function renderProgress(progress) {
    var current = Number(progress.current || 0);
    var total = Number(progress.total || state.totalRows || 0);
    var percent = Math.max(0, Math.min(100, Number(progress.percentage || 0)));
    el("task-progress-fill").style.width = percent + "%";
    el("task-progress-text").textContent = current.toLocaleString("zh-CN") + " / " + total.toLocaleString("zh-CN");
    el("task-progress-percent").textContent = percent.toFixed(percent % 1 ? 1 : 0) + "%";
    el("metric-elapsed").textContent = formatDuration(progress.elapsed_seconds);
    el("metric-eta").textContent = progress.eta_seconds == null ? "—" : formatDuration(progress.eta_seconds);
    el("metric-rate").textContent = progress.entries_per_minute ? progress.entries_per_minute.toLocaleString("zh-CN") + " 条/分" : "—";
    var usage = progress.token_usage || {};
    var totalTokens = usage.total_tokens || ((usage.prompt_tokens || usage.input_tokens || 0) + (usage.completion_tokens || usage.output_tokens || 0));
    el("metric-tokens").textContent = totalTokens ? Number(totalTokens).toLocaleString("zh-CN") : "—";
    el("control-note").hidden = !progress.control_note;
    el("control-note").textContent = progress.control_note || "";
    updateTaskStatus(progress.status, progress.error);
    updatePhases(progress.phase);
    if (["completed", "cancelled", "error"].includes(progress.status)) renderResult(progress);
}

function updatePhases(phase) {
    var order = ["analysis", "translation", "validation", "completed"];
    var currentIndex = Math.max(0, order.indexOf(phase));
    if (phase === "finalized" || phase === "stopping") currentIndex = 2;
    all("#phase-track > div").forEach(function (node, index) {
        node.classList.toggle("done", phase === "completed" ? true : index < currentIndex);
        node.classList.toggle("active", phase !== "completed" && index === currentIndex);
    });
}

function updateTaskStatus(status, error) {
    var labels = {
        idle: "未开始", starting: "正在启动", running: "处理中", paused: "已暂停新请求",
        stopping: "停止并写盘中", finalizing: "正在完成校验", completed: "处理完成", cancelled: "已停止并写盘", error: "任务异常",
    };
    var classes = {
        idle: "neutral", starting: "running", running: "running", paused: "warning",
        stopping: "warning", finalizing: "running", completed: "success", cancelled: "warning", error: "danger",
    };
    el("task-status-badge").className = "status-badge " + (classes[status] || "neutral");
    el("task-status-badge").textContent = labels[status] || status;
    el("task-subtitle").textContent = error || ({
        idle: "预检通过后即可启动",
        running: "总进度包含规则保留、断点恢复、模型翻译与合成条目",
        paused: "不会派发新请求，已发出的请求可能仍在返回",
        stopping: "正在等待已发出的请求结束并刷新输出文件",
        finalizing: "正在校验检查点、输出结构与复核报告",
        completed: "翻译、质量处理与复核报告已经生成",
        cancelled: "部分结果已写盘，可复核或导出部分结果",
        error: "已保存当前可用检查点，请查看错误后恢复任务",
    }[status] || "任务状态已更新");
}

function renderResult(progress) {
    el("result-summary").hidden = false;
    var summary = progress.review_summary || {};
    var needs = Number(summary.translated_needs_review || 0) + Number(summary.review_required || 0);
    var title = progress.status === "completed"
        ? (needs ? "翻译完成，仍有 " + needs + " 条需要人工复核" : "翻译与质量检查完成")
        : progress.status === "cancelled" ? "任务已停止，部分结果已安全写盘" : "任务异常结束，检查点已保留";
    el("result-title").textContent = title;
    el("result-description").textContent = progress.status === "completed"
        ? "“完成”表示流水线结束，不代表所有译文都已人工确认。"
        : "你可以先复核现有结果，或从历史与恢复中继续任务。";
    var metrics = [
        ["已翻译", summary.translated || 0],
        ["待复核", summary.translated_needs_review || 0],
        ["必须复核", summary.review_required || 0],
        ["规则保留", summary.preserved || 0],
    ];
    el("result-metrics").innerHTML = metrics.map(function (item) {
        return "<span>" + item[0] + " <b>" + Number(item[1]).toLocaleString("zh-CN") + "</b></span>";
    }).join("");
}

function updateActionStates(progress) {
    var status = state.taskStatus;
    var active = ["running", "paused", "stopping", "finalizing", "starting"].includes(status);
    el("btn-start").disabled = active || !state.preflight || !state.preflight.ok;
    el("btn-pause").disabled = status !== "running";
    el("btn-resume").disabled = status !== "paused";
    el("btn-stop").disabled = !["running", "paused"].includes(status);
    if (progress && progress.snapshot_ready) state.exportReady = true;
    var canExport = state.exportReady && !active && !aiReviewIsActive();
    el("btn-export").disabled = !canExport;
    el("btn-review-export").disabled = !canExport;
    lockFileControls(active);
}

async function pauseTranslation() {
    try {
        var result = await API.post("/translate/" + state.taskId + "/pause");
        state.taskStatus = result.status;
        updateTaskStatus(result.status);
        updateActionStates();
    } catch (error) { toast(error.message, "error"); }
}
async function resumeTranslation() {
    try {
        var result = await API.post("/translate/" + state.taskId + "/resume");
        state.taskStatus = result.status;
        updateTaskStatus(result.status);
        updateActionStates();
    } catch (error) { toast(error.message, "error"); }
}
async function stopTranslation() {
    if (!window.confirm("停止后会等待已发出的请求结束，并将当前结果写入检查点。是否继续？")) return;
    try {
        var result = await API.post("/translate/" + state.taskId + "/cancel");
        state.taskStatus = result.status;
        updateTaskStatus(result.status);
        updateActionStates();
        startPolling();
    } catch (error) { toast(error.message, "error"); }
}

function resetTaskDisplay() {
    el("task-progress-fill").style.width = "0%";
    el("task-progress-text").textContent = "0 / 0";
    el("task-progress-percent").textContent = "0%";
    ["metric-elapsed", "metric-eta", "metric-rate", "metric-tokens"].forEach(function (id) { el(id).textContent = "—"; });
    el("control-note").hidden = true;
    updateTaskStatus("idle");
    updatePhases("analysis");
    updateActionStates();
}

function activeFilePath() {
    return state.sourceFilePath || state.filePath;
}
