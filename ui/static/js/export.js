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
        var finalization = result.finalization || {};
        var finalizeResult = null;
        var cleanupConfirmed = false;
        if (finalization.confirmation_required && finalization.cleanup_token) {
            cleanupConfirmed = window.confirm(
                (finalization.pending_download ? "译文已生成，即将开始下载。" : "导出成功。")
                + "\n\n当前项目已完成全部复核，是否清理项目数据？"
                + "\n\n清理将删除内部工作副本、检查点和复核记录；外部原始文件、导出文件和备份不会删除。"
            );
            try {
                finalizeResult = await API.post("/export/finalize", {
                    cleanup_token: finalization.cleanup_token,
                    cleanup: cleanupConfirmed,
                });
            } catch (finalizeError) {
                cleanupConfirmed = false;
                toast("导出成功，但项目清理状态更新失败；历史记录已保留", "error");
            }
        }
        if (result.overwrote_original) {
            toast("已覆盖源文件：" + result.export_path, "success");
        } else if (destination.isDesktop) {
            toast("已导出到：" + result.export_path, "success");
        } else {
            var link = document.createElement("a");
            link.href = "/api/download?path=" + encodeURIComponent(result.export_path)
                + "&filename=" + encodeURIComponent(result.filename || state.fileName)
                + (cleanupConfirmed && finalization.pending_download
                    ? "&cleanup_token=" + encodeURIComponent(finalization.cleanup_token) : "");
            link.download = result.filename || state.fileName;
            document.body.appendChild(link);
            link.click();
            link.remove();
            toast(cleanupConfirmed && finalization.pending_download
                ? "最终结果已导出，下载完成后将自动清理项目"
                : "当前结果已导出；仍可继续复核并再次导出", "success");
        }
        if (finalizeResult && finalizeResult.cleaned) {
            resetFile();
            await Promise.all([loadHistory(), loadRecoveryBanner()]);
        } else if (cleanupConfirmed && finalization.pending_download) {
            resetFile();
            window.setTimeout(function () {
                Promise.all([loadHistory(), loadRecoveryBanner()]);
            }, 1000);
        } else if (cleanupConfirmed && finalizeResult && finalizeResult.error) {
            toast("导出成功，但自动清理失败；项目已保留，可在历史记录中手动清除", "error");
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
