"use strict";

async function chooseDesktopExportPath(defaultFilename) {
    var bridge = window.pywebview && window.pywebview.api;
    if (!bridge || typeof bridge.save_file_dialog !== "function") {
        return { isDesktop: false, path: "" };
    }
    var selectedPath = await bridge.save_file_dialog(defaultFilename);
    return { isDesktop: true, path: selectedPath || "" };
}

async function exportResult() {
    if (!state.sessionId || !state.filePath) {
        toast("当前任务不是可直接导出的上传会话", "error");
        return;
    }
    try {
        var destination = await chooseDesktopExportPath(state.fileName || "ManualTransFile.json");
        if (destination.isDesktop && !destination.path) {
            toast("已取消导出");
            return;
        }
        el("btn-export").disabled = true;
        el("btn-review-export").disabled = true;
        var result = await API.post("/export", {
            session_id: state.sessionId,
            file_path: state.filePath,
            file_type: "json",
            column_mappings: [],
            output_path: destination.path || null,
        });
        state.hasUnexportedResult = false;
        state.exportReady = true;
        if (destination.isDesktop) {
            toast("已导出到：" + result.export_path, "success");
        } else {
            var link = document.createElement("a");
            link.href = "/api/download?path=" + encodeURIComponent(result.export_path);
            link.download = result.filename || state.fileName;
            document.body.appendChild(link);
            link.click();
            link.remove();
            toast("当前结果已导出；仍可继续复核并再次导出", "success");
        }
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
