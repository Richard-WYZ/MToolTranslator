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
        state.history.projects = mergeHistoryProjects(recovery, history, tasks);
        renderHistoryProjects();
    } catch (error) {
        toast(error.message, "error");
    } finally {
        state.history.loading = false;
        syncHistoryActionState();
    }
}

function mergeHistoryProjects(recovery, checkpoints, tasks) {
    var byPath = new Map();
    function add(item, recoverable) {
        if (!item.file_path) return;
        var key = String(item.file_path).replace(/\\/g, "/").toLowerCase();
        var previous = byPath.get(key) || {};
        byPath.set(key, Object.assign({}, previous, item, {
            recoverable: Boolean(previous.recoverable || recoverable),
        }));
    }
    checkpoints.forEach(function (item) { add(item, false); });
    recovery.forEach(function (item) { add(item, true); });
    tasks.forEach(function (task) {
        var key = String(task.file_path || "").replace(/\\/g, "/").toLowerCase();
        var previous = byPath.get(key) || {};
        add(Object.assign({}, previous, {
            file_path: task.file_path, file_name: task.file_name || previous.file_name,
            model: task.model || previous.model, status: task.status || previous.status,
            task_id: task.task_id || task.id || "",
            updated_at: task.finished_at || task.started_at || previous.updated_at,
            completed: previous.completed == null ? Number(task.percentage || 0) : previous.completed,
            total: previous.total == null ? 100 : previous.total,
            review_queue_size: previous.review_queue_size == null
                ? Number((task.review_summary || {}).review_required || 0) + Number((task.review_summary || {}).translated_needs_review || 0)
                : previous.review_queue_size,
            file_exists: previous.file_exists !== false,
        }), false);
    });
    return Array.from(byPath.values()).map(function (item) {
        item.review_queue_size = Number(item.review_queue_size || 0)
            || Number(item.review_required || 0) + Number(item.translated_needs_review || 0);
        item.active = ["starting", "running", "paused", "stopping", "finalizing"].includes(item.status);
        item.recoverable = !item.active && Boolean(item.recoverable)
            && !["completed", "done", "exported"].includes(item.status);
        item.finished = ["completed", "done", "exported"].includes(item.status)
            || Boolean(item.total && item.completed >= item.total);
        return item;
    }).sort(function (left, right) {
        if (left.active !== right.active) return left.active ? -1 : 1;
        function timestamp(value) { return typeof value === "number" ? value * 1000 : Date.parse(value) || 0; }
        return timestamp(right.updated_at) - timestamp(left.updated_at);
    });
}

function renderHistoryProjects() {
    var query = el("history-search").value.trim().toLowerCase();
    var filter = el("history-status-filter").value || "all";
    var projects = state.history.projects || [];
    var rows = projects.filter(function (item) {
        var text = [item.project_display_name, item.project_name, item.file_name, item.original_path].join(" ").toLowerCase();
        if (query && !text.includes(query)) return false;
        if (filter === "unfinished") return !item.finished;
        if (filter === "review") return item.review_queue_size > 0;
        if (filter === "completed") return item.finished;
        return true;
    });
    el("history-count").textContent = rows.length + " / " + projects.length;
    if (!rows.length) {
        el("history-list").innerHTML = '<div class="empty-state compact">'
            + (projects.length ? "没有符合筛选条件的项目" : "还没有项目。导入文件后，进度会保存在这里。") + '</div>';
        return;
    }
    el("history-list").innerHTML = rows.map(function (item) {
        var percent = item.total ? Math.min(100, Math.round(item.completed * 100 / item.total)) : 0;
        var sourceHint = item.original_path || ("本地项目 " + (item.session_id || "未知"));
        var unavailable = item.file_exists === false;
        var status = item.active ? "翻译进行中" : item.recoverable ? "可继续翻译" : item.review_queue_size ? "有待检查项" : item.finished ? "翻译已完成" : "翻译未完成";
        var primaryAttribute = item.recoverable ? "data-resume-path" : "data-open-path";
        var primaryLabel = item.recoverable ? "继续翻译" : item.active ? "查看进度" : "查看结果";
        return '<article class="history-item"><div class="history-main"><strong title="' + escapeHtml(sourceHint) + '">'
            + escapeHtml(item.project_display_name || item.project_name || "未命名项目") + '</strong><span>'
            + escapeHtml(item.file_name || "ManualTransFile.json") + " · " + status + " · " + escapeHtml(formatDate(item.updated_at))
            + '</span><div class="history-progress"><div class="mini-progress"><i style="width:' + percent + '%"></i></div><span>'
            + percent + "%" + (item.review_queue_size ? " · 待检查 " + item.review_queue_size : "")
            + (unavailable ? " · 工作文件不可用" : "") + '</span></div></div><div class="history-actions">'
            + '<button class="btn btn-primary btn-sm" ' + primaryAttribute + '="' + escapeHtml(item.file_path)
            + '" data-path-unavailable="' + unavailable + '"' + (unavailable ? " disabled" : "") + '>' + primaryLabel + '</button>'
            + '<details class="history-more"><summary>更多</summary><div class="button-row">'
            + '<button class="btn btn-ghost btn-sm" data-rename-history-path="' + escapeHtml(item.file_path)
            + '" data-project-name="' + escapeHtml(item.project_name || "") + '" data-project-display-name="' + escapeHtml(item.project_display_name || "") + '">重命名</button>'
            + '<button class="btn btn-danger-ghost btn-sm" data-delete-history-path="' + escapeHtml(item.file_path) + '">删除记录</button></div>'
            + '<small class="history-source">' + escapeHtml(sourceHint) + " · " + escapeHtml(modelLabel(item.model)) + '</small></details></div></article>';
    }).join("");
    syncHistoryActionState();
}

function initHistoryWorkspace() {
    el("btn-refresh-history").addEventListener("click", loadHistory);
    el("history-search").addEventListener("input", renderHistoryProjects);
    el("history-status-filter").addEventListener("change", renderHistoryProjects);
    el("history-list").addEventListener("click", function (event) {
        var remove = event.target.closest("[data-delete-history-path]");
        var resume = event.target.closest("[data-resume-path]");
        var open = event.target.closest("[data-open-path]");
        var rename = event.target.closest("[data-rename-history-path]");
        if (remove) deleteHistory(remove.dataset.deleteHistoryPath);
        else if (resume) resumeHistory(resume.dataset.resumePath);
        else if (open) adoptHistoryFile(open.dataset.openPath);
        else if (rename) renameHistoryProject(rename.dataset.renameHistoryPath, rename.dataset.projectName, rename.dataset.projectDisplayName);
    });
}

async function adoptHistoryFile(path, options) {
    options = options || {};
    if (activeFilePath() && sameHistoryPath(activeFilePath(), path)) {
        if (options.navigate !== false) navigate(["starting", "running", "paused", "stopping", "finalizing"].includes(state.taskStatus) ? "translate" : "review");
        return true;
    }
    if (!(await ensureCanReplaceFile())) return false;
    try {
        // Check readability before changing the current workspace. The current
        // project's saved working copy and checkpoint remain in history.
        var preview = await API.get("/preview?path=" + encodeURIComponent(path) + "&limit=10");
        var item = (state.history.projects || []).find(function (project) { return sameHistoryPath(project.file_path, path); }) || {};
        resetFile();
        state.filePath = path;
        state.sourceFilePath = path;
        state.originalFilePath = item.original_path || "";
        state.fileName = item.file_name || path.split(/[\\/]/).pop();
        state.sessionId = item.session_id || path.split(/[\\/]/).slice(-2, -1)[0] || "";
        state.preview = preview;
        state.totalRows = preview.total_rows || 0;
        state.hasUnexportedResult = Number(item.completed || 0) > 0;
        renderFile();
        await refreshExportState();
        await refreshReviewCount();
        if (options.navigate !== false) navigate("review");
        return true;
    } catch (error) { toast(error.message, "error"); return false; }
}

async function resumeHistory(path) {
    if (settingsTaskIsActive()) { toast("请先等待当前任务结束", "error"); return; }
    try {
        if (!(await adoptHistoryFile(path, { navigate: false }))) return;
        var result = await API.post("/recovery/resume", { file_path: path, prompt_style: "professional" });
        state.taskId = result.task_id;
        state.taskStatus = "running";
        state.hasUnexportedResult = true;
        navigate("translate");
        startPolling();
        toast("已从上次进度继续翻译", "success");
    } catch (error) { toast(error.message, "error"); }
}

async function deleteHistory(path) {
    if (historyActionIsBusy()) return;
    var project = (state.history.projects || []).find(function (item) { return sameHistoryPath(item.file_path, path); });
    if (project && project.active || sameHistoryPath(activeFilePath(), path)
        && typeof settingsTaskIsActive === "function" && settingsTaskIsActive()) {
        toast("任务进行中，暂不能删除此记录", "error"); return;
    }
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
