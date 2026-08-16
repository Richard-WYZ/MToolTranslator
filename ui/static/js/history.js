"use strict";

function historyActionIsBusy() {
    return state.history.loading || state.history.clearingAll || state.history.deletingPaths.size > 0;
}

function sameHistoryPath(left, right) {
    return String(left || "").replace(/\\/g, "/").toLowerCase()
        === String(right || "").replace(/\\/g, "/").toLowerCase();
}

function syncHistoryActionState() {
    var busy = historyActionIsBusy();
    var status = el("history-busy-status");
    status.hidden = !busy;
    status.querySelector("span:last-child").textContent = state.history.clearingAll
        ? "正在清除历史记录，请稍候…"
        : (state.history.deletingPaths.size ? "正在删除历史记录，请稍候…" : "正在加载历史记录…");
    el("page-history").setAttribute("aria-busy", busy ? "true" : "false");
    el("btn-refresh-history").disabled = busy;
    el("btn-clear-history").disabled = busy || state.history.candidatePaths.size === 0;
    el("btn-clear-history").classList.toggle("is-loading", state.history.clearingAll);
    all("[data-delete-history-path], [data-resume-path], [data-open-path], [data-rename-history-path]").forEach(function (button) {
        var path = button.dataset.deleteHistoryPath || button.dataset.resumePath || button.dataset.openPath || button.dataset.renameHistoryPath || "";
        var deletingThis = Array.from(state.history.deletingPaths).some(function (item) { return sameHistoryPath(item, path); });
        button.disabled = busy || button.dataset.pathUnavailable === "true";
        button.classList.toggle("is-loading", deletingThis && Boolean(button.dataset.deleteHistoryPath));
    });
}

async function loadRecoveryBanner() {
    try {
        var payload = await API.get("/recovery/sessions");
        var sessions = payload.sessions || [];
        el("recovery-banner").hidden = sessions.length === 0;
        el("recovery-banner-text").textContent = sessions.length ? "最近任务已完成 " + sessions[0].completed + " / " + sessions[0].total + " 条。" : "";
    } catch (error) {
        el("recovery-banner").hidden = true;
    }
}

async function loadHistory() {
    if (state.history.loading) return;
    state.history.loading = true;
    syncHistoryActionState();
    try {
        var results = await Promise.all([
            API.get("/recovery/sessions"),
            API.get("/history/sessions"),
            API.get("/translate/tasks"),
        ]);
        var recovery = results[0].sessions || [];
        var history = results[1].sessions || [];
        var tasks = results[2].tasks || [];
        state.history.candidatePaths = new Set(
            recovery.concat(history).map(function (item) { return item.file_path; })
                .concat(tasks.map(function (item) { return item.file_path; })).filter(Boolean)
        );
        renderRecovery(recovery);
        renderHistory(history, tasks);
    } catch (error) {
        toast(error.message, "error");
    } finally {
        state.history.loading = false;
        syncHistoryActionState();
    }
}

function renderRecovery(sessions) {
    el("recovery-count").textContent = sessions.length;
    if (!sessions.length) {
        el("recovery-list").innerHTML = '<div class="empty-state compact">没有未完成的断点任务</div>';
        return;
    }
    el("recovery-list").innerHTML = sessions.map(function (session) {
        var percent = session.total ? Math.round(session.completed * 100 / session.total) : 0;
        var sourceHint = session.original_path || ("本地会话 " + (session.session_id || "未知"));
        return '<div class="history-item"><div class="history-main"><strong title="' + escapeHtml(sourceHint) + '">' + escapeHtml(session.project_display_name || "未命名项目") + "</strong><span>"
            + escapeHtml(session.file_name || "ManualTransFile.json") + " · " + escapeHtml(modelLabel(session.model)) + " · " + escapeHtml(formatDate(session.updated_at)) + '</span><span class="history-source" title="' + escapeHtml(sourceHint) + '">' + escapeHtml(sourceHint) + '</span><div class="history-progress"><div class="mini-progress"><i style="width:' + percent + '%"></i></div><span>' + session.completed + " / " + session.total + "</span></div></div>"
            + '<div class="history-actions"><button class="btn btn-ghost btn-sm" data-rename-history-path="' + escapeHtml(session.file_path) + '" data-project-name="' + escapeHtml(session.project_name || "") + '" data-project-display-name="' + escapeHtml(session.project_display_name || "") + '">命名</button><button class="btn btn-primary btn-sm" data-resume-path="' + escapeHtml(session.file_path) + '" data-path-unavailable="' + (session.file_exists ? "false" : "true") + '"' + (session.file_exists ? "" : " disabled") + '>继续</button><button class="btn btn-danger-ghost btn-sm" data-delete-history-path="' + escapeHtml(session.file_path) + '">删除</button></div></div>';
    }).join("");
}

function renderHistory(checkpoints, tasks) {
    var taskByPath = {};
    tasks.forEach(function (task) { taskByPath[task.file_path] = task; });
    if (!checkpoints.length && !tasks.length) {
        el("history-list").innerHTML = '<div class="empty-state compact">还没有翻译历史</div>';
        return;
    }
    var known = new Set();
    var rows = checkpoints.map(function (session) {
        known.add(session.file_path);
        var task = taskByPath[session.file_path];
        return {
            file_path: session.file_path,
            file_name: session.file_name,
            project_name: session.project_name,
            project_display_name: session.project_display_name,
            original_path: session.original_path,
            session_id: session.session_id,
            model: session.model,
            status: task ? task.status : session.status,
            completed: session.completed,
            total: session.total,
            updated_at: task && task.finished_at ? task.finished_at : session.updated_at,
            review_queue_size: session.review_queue_size
                || Number(session.review_required || 0) + Number(session.translated_needs_review || 0),
            file_exists: session.file_exists,
        };
    });
    tasks.forEach(function (task) {
        if (!known.has(task.file_path)) rows.push({
            file_path: task.file_path, file_name: task.file_name, model: task.model, status: task.status,
            project_name: "", project_display_name: "", original_path: "", session_id: "",
            completed: Math.round((task.percentage || 0)), total: 100, updated_at: task.finished_at || task.started_at,
            review_queue_size: Number((task.review_summary || {}).review_required || 0)
                + Number((task.review_summary || {}).translated_needs_review || 0),
            file_exists: true,
        });
    });
    el("history-list").innerHTML = rows.map(function (item) {
        var percent = item.total ? Math.round(item.completed * 100 / item.total) : 0;
        var sourceHint = item.original_path || ("本地会话 " + (item.session_id || "未知"));
        return '<div class="history-item"><div class="history-main"><strong title="' + escapeHtml(sourceHint) + '">' + escapeHtml(item.project_display_name || "未命名项目") + "</strong><span>"
            + escapeHtml(item.file_name || "ManualTransFile.json") + " · " + escapeHtml(modelLabel(item.model)) + " · " + escapeHtml(statusLabel(item.status)) + " · " + escapeHtml(formatDate(item.updated_at)) + '</span><span class="history-source" title="' + escapeHtml(sourceHint) + '">' + escapeHtml(sourceHint) + '</span><div class="history-progress"><div class="mini-progress"><i style="width:' + percent + '%"></i></div><span>' + percent + "% · 待复核 " + item.review_queue_size + "</span></div></div>"
            + '<div class="history-actions"><button class="btn btn-ghost btn-sm" data-rename-history-path="' + escapeHtml(item.file_path) + '" data-project-name="' + escapeHtml(item.project_name || "") + '" data-project-display-name="' + escapeHtml(item.project_display_name || "") + '">命名</button><button class="btn btn-secondary btn-sm" data-open-path="' + escapeHtml(item.file_path) + '" data-path-unavailable="' + (item.file_exists === false ? "true" : "false") + '"' + (item.file_exists === false ? " disabled" : "") + '>打开复核</button><button class="btn btn-danger-ghost btn-sm" data-delete-history-path="' + escapeHtml(item.file_path) + '">删除</button></div></div>';
    }).join("");
}

async function adoptHistoryFile(path) {
    state.filePath = path;
    state.sourceFilePath = path;
    state.originalFilePath = "";
    state.fileName = path.split(/[\\/]/).pop();
    state.sessionId = path.split(/[\\/]/).slice(-2, -1)[0] || "";
    try {
        await loadPreview();
        renderFile();
        await refreshExportState();
        await refreshReviewCount();
        navigate("review");
    } catch (error) { toast(error.message, "error"); }
}

async function resumeHistory(path) {
    if (["running", "paused", "stopping", "finalizing"].includes(state.taskStatus)) {
        toast("已有任务正在运行", "error");
        return;
    }
    try {
        await adoptHistoryFile(path);
        var result = await API.post("/recovery/resume", { file_path: path, prompt_style: "professional" });
        state.taskId = result.task_id;
        state.taskStatus = "running";
        state.hasUnexportedResult = true;
        navigate("translate");
        startPolling();
        toast("已按检查点配置恢复任务", "success");
    } catch (error) { toast(error.message, "error"); }
}

async function deleteHistory(path) {
    if (historyActionIsBusy()) return;
    var fileName = path.split(/[\\/]/).pop() || "未命名任务";
    var confirmed = window.confirm(
        "确定删除“" + fileName + "”的历史记录吗？\n\n"
        + "将删除检查点、翻译快照和复核数据。已导出的文件与外部原始文件不会删除。此操作无法撤销。"
    );
    if (!confirmed) return;
    state.history.deletingPaths.add(path);
    syncHistoryActionState();
    try {
        await API.delete("/history/session?file_path=" + encodeURIComponent(path));
        if (activeFilePath() && sameHistoryPath(activeFilePath(), path)) resetFile();
        await Promise.all([loadHistory(), loadRecoveryBanner()]);
        toast("历史记录已删除", "success");
    } catch (error) {
        toast(error.message, "error");
    } finally {
        state.history.deletingPaths.delete(path);
        syncHistoryActionState();
    }
}

async function renameHistoryProject(path, currentName, displayName) {
    if (historyActionIsBusy()) return;
    var nextName = window.prompt(
        "设置项目名称（最多 80 个字符）\n\n留空会恢复为目录名或会话编号。",
        currentName || displayName || ""
    );
    if (nextName === null) return;
    state.history.loading = true;
    syncHistoryActionState();
    try {
        await API.put("/history/session/name", { file_path: path, project_name: nextName });
        toast(nextName.trim() ? "项目名称已更新" : "已恢复自动项目名称", "success");
    } catch (error) {
        toast(error.message, "error");
    } finally {
        state.history.loading = false;
        await loadHistory();
    }
}

async function clearAllHistory() {
    if (historyActionIsBusy() || state.history.candidatePaths.size === 0) return;
    var count = state.history.candidatePaths.size;
    var confirmed = window.confirm(
        "确定清除全部 " + count + " 项历史记录吗？\n\n"
        + "将清除未运行任务的检查点、翻译快照和复核数据；正在运行的任务会跳过。"
        + "已导出的文件与外部原始文件不会删除。此操作无法撤销。"
    );
    if (!confirmed) return;
    state.history.clearingAll = true;
    syncHistoryActionState();
    try {
        var result = await API.post("/history/clear", {});
        var activePath = activeFilePath();
        if (activePath && (result.deleted_paths || []).some(function (path) { return sameHistoryPath(path, activePath); })) {
            resetFile();
        }
        await Promise.all([loadHistory(), loadRecoveryBanner()]);
        var message = "已清除 " + result.deleted + " 项历史";
        if (result.skipped) message += "，跳过 " + result.skipped + " 项运行中任务";
        if (result.failed) message += "，" + result.failed + " 项失败";
        toast(message, result.failed ? "error" : "success");
    } catch (error) {
        toast(error.message, "error");
    } finally {
        state.history.clearingAll = false;
        syncHistoryActionState();
    }
}
