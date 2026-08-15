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
        var bridge = window.pywebview && window.pywebview.api;
        var isDesktop = Boolean(bridge && typeof bridge.save_file_dialog === "function");
        var overwriteOriginal = false;
        var destination = { isDesktop: isDesktop, path: "" };
        if (isDesktop && state.originalFilePath) {
            overwriteOriginal = window.confirm(
                "是否用当前翻译结果覆盖源文件？\n\n" + state.originalFilePath
                + "\n\n首次覆盖会在源文件旁保留 .bak 备份。"
            );
        }
        if (!overwriteOriginal && isDesktop) {
            destination = await chooseDesktopExportPath(state.fileName || "ManualTransFile.json");
            if (!destination.path) {
                toast("已取消导出");
                return;
            }
        }
        el("btn-export").disabled = true;
        el("btn-review-export").disabled = true;
        var payload = {
            session_id: state.sessionId, file_path: state.filePath, file_type: "json",
            column_mappings: [], output_path: destination.path || null,
            overwrite_original: overwriteOriginal,
        };
        var result;
        try {
            result = await API.post("/export", payload);
        } catch (error) {
            if (!overwriteOriginal || !isDesktop) throw error;
            toast(error.message + "；请选择其他保存位置", "error");
            destination = await chooseDesktopExportPath(state.fileName || "ManualTransFile.json");
            if (!destination.path) {
                toast("已取消导出");
                return;
            }
            payload.overwrite_original = false;
            payload.output_path = destination.path;
            result = await API.post("/export", payload);
            overwriteOriginal = false;
        }
        state.hasUnexportedResult = false;
        state.exportReady = true;
        if (result.overwrote_original) {
            toast("已覆盖源文件：" + result.export_path, "success");
        } else if (destination.isDesktop) {
            toast("已导出到：" + result.export_path, "success");
        } else {
            var link = document.createElement("a");
            link.href = "/api/download?path=" + encodeURIComponent(result.export_path)
                + "&filename=" + encodeURIComponent(result.filename || state.fileName);
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
        state.originalFilePath = result.original_path || "";
    } catch (error) {
        state.exportReady = false;
    }
    updateActionStates();
}
