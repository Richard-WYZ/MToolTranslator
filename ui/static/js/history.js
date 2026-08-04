"use strict";

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
    try {
        var results = await Promise.all([
            API.get("/recovery/sessions"),
            API.get("/history/sessions"),
            API.get("/translate/tasks"),
        ]);
        renderRecovery(results[0].sessions || []);
        renderHistory(results[1].sessions || [], results[2].tasks || []);
    } catch (error) { toast(error.message, "error"); }
}

function renderRecovery(sessions) {
    el("recovery-count").textContent = sessions.length;
    if (!sessions.length) {
        el("recovery-list").innerHTML = '<div class="empty-state compact">没有未完成的断点任务</div>';
        return;
    }
    el("recovery-list").innerHTML = sessions.map(function (session) {
        var percent = session.total ? Math.round(session.completed * 100 / session.total) : 0;
        return '<div class="history-item"><div class="history-main"><strong title="' + escapeHtml(session.file_path) + '">' + escapeHtml(session.file_name || "未命名任务") + "</strong><span>"
            + escapeHtml(modelLabel(session.model)) + " · " + escapeHtml(formatDate(session.updated_at)) + '</span><div class="history-progress"><div class="mini-progress"><i style="width:' + percent + '%"></i></div><span>' + session.completed + " / " + session.total + "</span></div></div>"
            + '<div class="history-actions"><button class="btn btn-primary btn-sm" data-resume-path="' + escapeHtml(session.file_path) + '"' + (session.file_exists ? "" : " disabled") + ">继续</button></div></div>";
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
            completed: Math.round((task.percentage || 0)), total: 100, updated_at: task.finished_at || task.started_at,
            review_queue_size: Number((task.review_summary || {}).review_required || 0)
                + Number((task.review_summary || {}).translated_needs_review || 0),
            file_exists: true,
        });
    });
    el("history-list").innerHTML = rows.map(function (item) {
        var percent = item.total ? Math.round(item.completed * 100 / item.total) : 0;
        return '<div class="history-item"><div class="history-main"><strong title="' + escapeHtml(item.file_path) + '">' + escapeHtml(item.file_name || "未命名任务") + "</strong><span>"
            + escapeHtml(modelLabel(item.model)) + " · " + escapeHtml(statusLabel(item.status)) + " · " + escapeHtml(formatDate(item.updated_at)) + '</span><div class="history-progress"><div class="mini-progress"><i style="width:' + percent + '%"></i></div><span>' + percent + "% · 待复核 " + item.review_queue_size + "</span></div></div>"
            + '<div class="history-actions"><button class="btn btn-secondary btn-sm" data-open-path="' + escapeHtml(item.file_path) + '"' + (item.file_exists === false ? " disabled" : "") + '>打开复核</button></div></div>';
    }).join("");
}

async function adoptHistoryFile(path) {
    state.filePath = path;
    state.sourceFilePath = path;
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
