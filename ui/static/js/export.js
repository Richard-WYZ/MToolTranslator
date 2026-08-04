"use strict";

async function exportResult() {
    if (!state.sessionId || !state.filePath) {
        toast("当前任务不是可直接导出的上传会话", "error");
        return;
    }
    el("btn-export").disabled = true;
    el("btn-review-export").disabled = true;
    try {
        var result = await API.post("/export", {
            session_id: state.sessionId,
            file_path: state.filePath,
            file_type: "json",
            column_mappings: [],
        });
        state.hasUnexportedResult = false;
        state.exportReady = true;
        var link = document.createElement("a");
        link.href = "/api/download?path=" + encodeURIComponent(result.export_path);
        link.download = result.filename || state.fileName;
        document.body.appendChild(link);
        link.click();
        link.remove();
        toast("当前结果已导出；仍可继续复核并再次导出", "success");
    } catch (error) {
        toast(error.message, "error");
    } finally {
        updateActionStates();
    }
}

async function refreshExportState() {
    if (!activeFilePath()) {
        state.exportReady = false;
        updateActionStates();
        return;
    }
    try {
        var result = await API.get("/export/status?file_path=" + encodeURIComponent(activeFilePath()));
        state.exportReady = Boolean(result.ready);
        state.hasUnexportedResult = Boolean(result.dirty);
    } catch (error) {
        state.exportReady = false;
    }
    updateActionStates();
}
