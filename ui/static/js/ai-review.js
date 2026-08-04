"use strict";

function aiReviewIsActive() {
    return ["preparing", "reviewing", "verifying", "applying", "finalizing", "stopping"].includes(state.review.aiTaskStatus);
}

function aiReviewRequest(scope, rows) {
    return {
        file_path: activeFilePath(), scope: scope, rows: rows || [], filter: state.review.filter,
        review_model: el("ai-review-model").value || "auto",
        verifier_model: el("ai-verifier-model").value || "auto",
        sensitive_model: el("ai-sensitive-model").value || "auto", auto_apply: true,
    };
}

async function startAIReview(scope) {
    if (!activeFilePath() || aiReviewIsActive() || reviewActionIsBusy()) return;
    var requestedScope = scope;
    var rows = scope === "selected" ? Array.from(state.review.selectedRows) : [];
    if (scope === "row" && state.review.selectedRow != null) { scope = "selected"; rows = [state.review.selectedRow]; }
    if (scope === "selected" && !rows.length) { toast("请先选择需要 AI 复核的条目", "error"); return; }
    var payload = aiReviewRequest(scope, rows);
    var buttonId = requestedScope === "row" ? "btn-ai-review-row" : requestedScope === "selected" ? "btn-ai-review-selected" : "btn-ai-review-start";
    setReviewActionBusy("ai_start", buttonId);
    try {
        var preflight = await API.post("/review/ai/preflight", payload);
        if (!preflight.counts || !preflight.counts.total) { toast("当前范围没有可复核条目", "error"); return; }
        var message = "将 AI 复核 " + preflight.counts.total + " 条译文（必须复核 " + preflight.counts.required
            + "，建议复核 " + preflight.counts.advisory + "）。\n\n主模型：" + modelLabel(preflight.models.review)
            + "\n验证模型：" + modelLabel(preflight.models.verifier)
            + (preflight.counts.sensitive ? "\n敏感内容：" + modelLabel(preflight.models.sensitive) : "")
            + "\n预计请求：约 " + preflight.estimated_requests
            + "\n预计 Token：约 " + Number(preflight.estimated_tokens || 0).toLocaleString("zh-CN")
            + "\n\n只会自动应用通过硬性校验和第二模型确认的结果。是否开始？";
        if (!window.confirm(message)) return;
        var task = await API.post("/review/ai/start", payload);
        state.review.aiTaskId = task.task_id; state.review.aiTaskStatus = task.status;
        renderAIReviewProgress(task); startAIReviewPolling();
    } catch (error) { toast(error.message, "error"); }
    finally { setReviewActionBusy(); }
}

async function loadCurrentAIReview() {
    if (!activeFilePath()) return;
    try {
        var payload = await API.get("/review/ai/current?file_path=" + encodeURIComponent(activeFilePath()));
        if (!payload.task) { renderAIReviewProgress(null); return; }
        state.review.aiTaskId = payload.task.task_id; state.review.aiTaskStatus = payload.task.status;
        renderAIReviewProgress(payload.task);
        if (aiReviewIsActive()) startAIReviewPolling();
    } catch (error) { renderAIReviewProgress(null); }
}

function startAIReviewPolling() {
    stopAIReviewPolling();
    var generation = ++state.review.aiPollingGeneration;
    pollAIReview(generation);
}

function stopAIReviewPolling() {
    if (state.review.aiPollingTimer) clearTimeout(state.review.aiPollingTimer);
    state.review.aiPollingTimer = null; state.review.aiPollingGeneration += 1;
}

async function pollAIReview(generation) {
    if (generation !== state.review.aiPollingGeneration || !state.review.aiTaskId) return;
    try {
        var task = await API.get("/review/ai/" + encodeURIComponent(state.review.aiTaskId) + "/progress");
        var previous = state.review.aiTaskStatus;
        state.review.aiTaskStatus = task.status; state.review.aiProgress = task; renderAIReviewProgress(task);
        if (["completed", "cancelled", "error"].includes(task.status)) {
            stopAIReviewPolling();
            if (task.status !== previous) {
                if (Number((task.counts || {}).applied || 0) > 0) {
                    state.hasUnexportedResult = true;
                    state.exportReady = true;
                }
                clearReviewSelection();
                await refreshReviewAfterEdit();
                toast(task.status === "completed" ? "AI 复核完成" : task.status === "cancelled" ? "AI 复核已停止" : "AI 复核失败：" + task.error, task.status === "completed" ? "success" : "error");
            }
            return;
        }
    } catch (error) { toast("读取 AI 复核进度失败：" + error.message, "error"); }
    if (generation === state.review.aiPollingGeneration) state.review.aiPollingTimer = setTimeout(function () { pollAIReview(generation); }, 1200);
}

function renderAIReviewProgress(task) {
    task = task || { status: "idle", phase: "idle", current: 0, total: 0, percentage: 0, counts: {}, token_usage: {} };
    state.review.aiTaskStatus = task.status || "idle"; state.review.aiProgress = task;
    var labels = { idle: "未开始", preparing: "准备中", reviewing: "模型复核中", verifying: "第二模型验证中",
        applying: "正在安全应用", finalizing: "最终校验", stopping: "正在停止", completed: "已完成",
        cancelled: "已停止", interrupted: "已中断，可继续", error: "失败" };
    var badge = el("ai-review-status");
    badge.textContent = labels[task.status] || task.status;
    badge.className = "status-badge " + (task.status === "completed" ? "success" : task.status === "error" ? "danger" : aiReviewIsActive() ? "warning is-working" : "neutral");
    el("ai-review-progress").hidden = !Boolean(task.total || task.status !== "idle");
    var percent = Math.max(0, Math.min(100, Number(task.percentage || 0)));
    el("ai-review-progress-fill").style.width = percent + "%";
    el("ai-review-progress-text").textContent = Number(task.current || 0).toLocaleString("zh-CN") + " / " + Number(task.total || 0).toLocaleString("zh-CN");
    el("ai-review-progress-percent").textContent = percent.toFixed(percent % 1 ? 1 : 0) + "%";
    var counts = task.counts || {}, usage = task.token_usage || {};
    el("ai-review-summary").innerHTML = [
        '<span class="good">已修复 ' + Number(counts.fixed || 0) + "</span>",
        '<span class="good">确认原译文 ' + Number(counts.confirmed || 0) + "</span>",
        '<span class="warn">仍无法确认 ' + Number(counts.unresolved || 0) + "</span>",
        "<span>已应用 " + Number(counts.applied || 0) + "</span>",
        "<span>Token " + Number(usage.total_tokens || 0).toLocaleString("zh-CN") + "</span>",
        "<span>用时 " + formatDuration(task.elapsed_seconds || 0) + "</span>",
    ].join("");
    var active = aiReviewIsActive(), busy = reviewActionIsBusy(), locked = active || busy;
    el("btn-ai-review-start").disabled = locked;
    el("btn-ai-review-selected").disabled = locked || state.review.selectedRows.size === 0;
    el("btn-ai-review-row").disabled = locked;
    el("btn-review-export").disabled = locked || !state.exportReady;
    el("btn-ai-review-stop").hidden = !active || task.status === "stopping";
    el("btn-ai-review-resume").hidden = !task.can_resume;
    el("btn-ai-review-rollback").hidden = !task.can_rollback;
    ["ai-review-scope", "ai-review-model", "ai-verifier-model", "ai-sensitive-model"].forEach(function (id) { el(id).disabled = locked; });
    ["btn-review-accept", "btn-review-draft", "btn-review-preserve", "btn-accept-selected"].forEach(function (id) { el(id).disabled = locked || (id === "btn-accept-selected" && state.review.selectedRows.size === 0); });
    el("editor-target").readOnly = locked;
    all("[data-review-check]").forEach(function (node) {
        var item = state.review.items.find(function (entry) { return entry.row === Number(node.dataset.reviewCheck); });
        node.disabled = locked || !reviewItemIsSelectable(item);
    });
}

async function stopAIReview() {
    if (!state.review.aiTaskId || !aiReviewIsActive() || reviewActionIsBusy()) return;
    setReviewActionBusy("ai_stop", "btn-ai-review-stop");
    try {
        var task = await API.post("/review/ai/" + encodeURIComponent(state.review.aiTaskId) + "/cancel", {});
        state.review.aiTaskStatus = task.status; renderAIReviewProgress(task);
    } catch (error) { toast(error.message, "error"); }
    finally { setReviewActionBusy(); }
}

async function resumeAIReview() {
    if (!state.review.aiTaskId || aiReviewIsActive() || reviewActionIsBusy()) return;
    setReviewActionBusy("ai_resume", "btn-ai-review-resume");
    try {
        var task = await API.post("/review/ai/" + encodeURIComponent(state.review.aiTaskId) + "/resume", { file_path: activeFilePath() });
        state.review.aiTaskStatus = task.status; renderAIReviewProgress(task); startAIReviewPolling();
    } catch (error) { toast(error.message, "error"); }
    finally { setReviewActionBusy(); }
}

async function rollbackAIReview() {
    if (reviewActionIsBusy()) return;
    if (!state.review.aiTaskId || !window.confirm("撤销本次 AI 复核已自动应用的译文？之后手动修改过的条目不会被覆盖。")) return;
    setReviewActionBusy("ai_rollback", "btn-ai-review-rollback");
    try {
        var result = await API.post("/review/ai/" + encodeURIComponent(state.review.aiTaskId) + "/rollback", { file_path: activeFilePath() });
        toast("已恢复 " + result.restored + " 条；跳过 " + result.skipped + " 条", "success");
        if (result.restored) { state.hasUnexportedResult = true; state.exportReady = true; }
        clearReviewSelection();
        await refreshReviewAfterEdit(); await loadCurrentAIReview();
    } catch (error) { toast(error.message, "error"); }
    finally { setReviewActionBusy(); }
}
