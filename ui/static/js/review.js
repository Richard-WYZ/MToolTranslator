"use strict";

function reviewActionIsBusy() {
    return Boolean(state.review.actionBusy);
}

function setReviewActionBusy(kind, buttonId) {
    var workspace = el("review-workspace");
    var overlay = el("review-busy-overlay");
    var previousButton = state.review.actionBusyButton ? el(state.review.actionBusyButton) : null;
    if (previousButton) previousButton.classList.remove("is-loading");
    state.review.actionBusy = kind || "";
    state.review.actionBusyButton = kind && buttonId ? buttonId : "";
    var labels = {
        ai_start: "正在准备 AI 复核…",
        ai_stop: "正在停止 AI 复核…",
        ai_resume: "正在恢复 AI 复核…",
        ai_rollback: "正在撤销 AI 复核结果…",
        save: "正在保存当前译文…",
        batch_save: "正在确认所选译文…",
    };
    workspace.setAttribute("aria-busy", kind ? "true" : "false");
    overlay.hidden = !kind;
    if (kind) el("review-busy-label").textContent = labels[kind] || "正在处理复核操作…";
    all("button, input, textarea, select, summary", workspace).forEach(function (node) {
        if (node.closest("#review-busy-overlay")) return;
        if (kind) {
            if (!node.hasAttribute("data-review-busy-disabled")) node.dataset.reviewBusyDisabled = node.disabled ? "1" : "0";
            node.disabled = true;
        } else if (node.hasAttribute("data-review-busy-disabled")) {
            node.disabled = node.dataset.reviewBusyDisabled === "1";
            delete node.dataset.reviewBusyDisabled;
        }
    });
    var currentButton = state.review.actionBusyButton ? el(state.review.actionBusyButton) : null;
    if (currentButton) currentButton.classList.add("is-loading");
    if (!kind) {
        renderAIReviewProgress(state.review.aiProgress);
        renderReviewSelectionControls();
    }
}

async function refreshReviewCount() {
    if (!activeFilePath()) return;
    try {
        var stats = await API.get("/review/stats?file_path=" + encodeURIComponent(activeFilePath()));
        var count = Number(stats.required_review || 0);
        el("nav-review-count").hidden = count === 0;
        el("nav-review-count").textContent = count;
    } catch (error) {
        // 尚无翻译输出时无需提示。
    }
}

async function loadReview() {
    var path = activeFilePath();
    var requestId = ++state.review.loadRequestId;
    if (!path) {
        el("review-empty").hidden = false;
        el("review-workspace").hidden = true;
        return;
    }
    if (state.review.selectionFilePath !== path) resetReviewSelection(path);
    try {
        var stats = await API.get("/review/stats?file_path=" + encodeURIComponent(path));
        if (requestId !== state.review.loadRequestId || path !== activeFilePath()) return;
        state.review.stats = stats;
        el("review-empty").hidden = false;
        el("review-workspace").hidden = true;
        if (!stats.total) return;
        el("review-empty").hidden = true;
        el("review-workspace").hidden = false;
        renderReviewStats(stats);
        populateAIReviewModels();
        await loadCurrentAIReview();
        await refreshExportState();
        await loadReviewList(state.review.offset);
    } catch (error) {
        if (requestId !== state.review.loadRequestId) return;
        el("review-empty").hidden = false;
        el("review-workspace").hidden = true;
        toast(error.message, "error");
    }
}

function renderReviewStats(stats) {
    el("review-stat-required").textContent = Number(stats.required_review || 0).toLocaleString("zh-CN");
    el("review-stat-advisory").textContent = Number(stats.advisory_review || 0).toLocaleString("zh-CN");
    el("review-stat-preserved").textContent = Number(stats.system_preserved || 0).toLocaleString("zh-CN");
    el("review-stat-done").textContent = Number(stats.confirmed_translation || 0).toLocaleString("zh-CN");
    el("review-count-required").textContent = Number(stats.required_review || 0).toLocaleString("zh-CN");
    el("review-count-advisory").textContent = Number(stats.advisory_review || 0).toLocaleString("zh-CN");
    el("review-count-preserved").textContent = Number(stats.system_preserved || 0).toLocaleString("zh-CN");
    el("review-count-actionable").textContent = Number(stats.needs_review || 0).toLocaleString("zh-CN");
    el("nav-review-count").hidden = !stats.required_review;
    el("nav-review-count").textContent = stats.required_review || 0;
}

function populateAIReviewModels() {
    var models = (state.models || []).map(function (item) { return item.id || item.name || ""; }).filter(Boolean);
    [
        ["ai-review-model", "自动选择（推荐）"],
        ["ai-verifier-model", "自动选择不同模型"],
        ["ai-sensitive-model", "自动选择已测试模型"],
    ].forEach(function (config) {
        var select = el(config[0]);
        var saved = select.value || localStorage.getItem("lgt." + config[0]) || "auto";
        select.innerHTML = '<option value="auto">' + config[1] + "</option>"
            + models.map(function (model) { return '<option value="' + escapeHtml(model) + '">' + escapeHtml(modelLabel(model)) + "</option>"; }).join("");
        select.value = models.includes(saved) ? saved : "auto";
    });
}

async function loadReviewList(offset) {
    var path = activeFilePath();
    if (!path) return;
    state.review.offset = Math.max(0, Number(offset || 0));
    var requestedOffset = state.review.offset;
    var requestedFilter = state.review.filter;
    var requestId = ++state.review.listRequestId;
    if (state.review.listController) state.review.listController.abort();
    var controller = new AbortController();
    state.review.listController = controller;
    el("review-queue").innerHTML = '<div class="empty-state compact">正在加载复核条目…</div>';
    el("btn-review-prev").disabled = true;
    el("btn-review-next").disabled = true;
    var payload;
    try {
        payload = await API.get(
            "/review/list?file_path=" + encodeURIComponent(path)
            + "&offset=" + requestedOffset
            + "&limit=" + state.review.limit
            + "&filter=" + encodeURIComponent(requestedFilter),
            { signal: controller.signal }
        );
    } catch (error) {
        if (error && error.name === "AbortError") return;
        throw error;
    } finally {
        if (requestId === state.review.listRequestId) state.review.listController = null;
    }
    if (requestId !== state.review.listRequestId || path !== activeFilePath() || requestedFilter !== state.review.filter) return;
    state.review.items = payload.items || [];
    state.review.items.forEach(function (item) {
        if (state.review.selectedRows.has(item.row)) state.review.selectedItems.set(item.row, item);
    });
    state.review.total = payload.matched_total || 0;
    state.review.offset = payload.offset || 0;
    renderReviewQueue();
    if (state.review.items.length) {
        var selected = state.review.items.find(function (item) { return item.row === state.review.selectedRow; }) || state.review.items[0];
        selectReviewRow(selected.row);
    } else {
        state.review.selectedRow = null;
        el("review-editor-empty").hidden = false;
        el("review-editor").hidden = true;
    }
}

function reviewColumn(item) {
    return item && item.columns && item.columns[0] ? item.columns[0] : {};
}

function reviewItemIsSelectable(item) {
    return ["review_required", "translated_needs_review"].includes(reviewColumn(item).status);
}

function resetReviewSelection(filePath) {
    state.review.selectedRows.clear();
    state.review.selectedItems.clear();
    state.review.selectionFilePath = filePath || "";
    renderReviewSelectionControls();
}

function setReviewRowSelected(row, selected, item) {
    if (selected && (!item || !reviewItemIsSelectable(item))) return;
    if (selected) {
        state.review.selectedRows.add(row);
        if (item) state.review.selectedItems.set(row, item);
    } else {
        state.review.selectedRows.delete(row);
        state.review.selectedItems.delete(row);
    }
    renderReviewSelectionControls();
}

function toggleCurrentReviewPage(selected) {
    if (reviewActionIsBusy() || aiReviewIsActive()) return;
    state.review.items.filter(reviewItemIsSelectable).forEach(function (item) { setReviewRowSelected(item.row, selected, item); });
    all("[data-review-check]:not(:disabled)").forEach(function (node) { node.checked = selected; });
    renderReviewSelectionControls();
}

function clearReviewSelection() {
    state.review.selectedRows.clear();
    state.review.selectedItems.clear();
    all("[data-review-check]").forEach(function (node) { node.checked = false; });
    renderReviewSelectionControls();
}

function renderReviewSelectionControls() {
    var rows = state.review.items.filter(reviewItemIsSelectable).map(function (item) { return item.row; });
    var selectedOnPage = rows.filter(function (row) { return state.review.selectedRows.has(row); }).length;
    var selectPage = el("review-select-page");
    if (!selectPage) return;
    var locked = reviewActionIsBusy() || aiReviewIsActive();
    selectPage.disabled = !rows.length || locked;
    selectPage.checked = Boolean(rows.length) && selectedOnPage === rows.length;
    selectPage.indeterminate = selectedOnPage > 0 && selectedOnPage < rows.length;
    el("review-selected-count").textContent = "已选 " + state.review.selectedRows.size.toLocaleString("zh-CN") + " 项";
    el("btn-review-clear-selection").disabled = state.review.selectedRows.size === 0 || locked;
    el("btn-accept-selected").disabled = state.review.selectedRows.size === 0 || locked;
    el("btn-ai-review-selected").disabled = state.review.selectedRows.size === 0 || locked;
}

function renderReviewQueue() {
    var queue = el("review-queue");
    el("review-queue-count").textContent = state.review.total.toLocaleString("zh-CN") + " 项";
    if (!state.review.items.length) {
        queue.innerHTML = '<div class="empty-state compact">当前筛选下没有条目</div>';
    } else {
        queue.innerHTML = state.review.items.map(function (item) {
            var column = reviewColumn(item);
            var risk = column.is_refusal || column.status === "review_required" ? "danger" : "";
            var checked = state.review.selectedRows.has(item.row) ? " checked" : "";
            var selectable = reviewItemIsSelectable(item) && !reviewActionIsBusy() && !aiReviewIsActive();
            var aiStatus = (column.ai_review || {}).status || "";
            var aiLabel = { fixed: "AI已修复", confirmed: "AI已确认", unresolved: "AI未解决", conflict: "AI冲突" }[aiStatus] || "";
            return '<div class="queue-item' + (item.row === state.review.selectedRow ? " selected" : "") + '" data-review-row="' + item.row + '">'
                + '<input type="checkbox" data-review-check="' + item.row + '"' + checked + (selectable ? "" : " disabled") + ' aria-label="选择第 ' + (item.row + 1) + ' 行">'
                + '<div class="queue-copy"><div class="queue-meta"><span><i class="risk-dot ' + risk + '"></i>第 ' + (item.row + 1) + ' 行' + (aiLabel ? '<i class="queue-ai-badge">' + aiLabel + "</i>" : "") + '</span><span>' + escapeHtml(statusLabel(column.status)) + "</span></div>"
                + "<p>" + escapeHtml(truncate(column.original, 42)) + "</p>"
                + '<p class="target">' + escapeHtml(truncate(column.translated, 42) || (column.status === "pending" ? "翻译中 / 尚未处理" : "尚无译文")) + "</p></div></div>";
        }).join("");
    }
    var page = Math.floor(state.review.offset / state.review.limit) + 1;
    var pages = Math.max(1, Math.ceil(state.review.total / state.review.limit));
    el("review-page-label").textContent = page + " / " + pages;
    el("btn-review-prev").disabled = state.review.offset <= 0;
    el("btn-review-next").disabled = state.review.offset + state.review.limit >= state.review.total;
    renderReviewSelectionControls();
}

function selectReviewRow(row) {
    var item = state.review.items.find(function (entry) { return entry.row === Number(row); });
    if (!item) return;
    state.review.selectedRow = item.row;
    all(".queue-item").forEach(function (node) { node.classList.toggle("selected", Number(node.dataset.reviewRow) === item.row); });
    renderReviewEditor(item);
}

function renderReviewEditor(item) {
    var column = reviewColumn(item);
    el("review-editor-empty").hidden = true;
    el("review-editor").hidden = false;
    el("editor-row").textContent = "第 " + (item.row + 1) + " 行";
    el("editor-status").textContent = statusLabel(column.status);
    el("editor-source").textContent = column.original || "";
    el("editor-target").value = column.translated || "";
    var issues = (column.violations || []).slice();
    if (!issues.length && (column.review_reasons || []).length) {
        issues = column.review_reasons.map(function (reason) {
            return { type: "review_reason", message: String(reason) };
        });
    }
    if (!issues.length && ["review_required", "translated_needs_review"].includes(column.status)) {
        issues.push({ type: "review_status", message: "检查点状态要求人工确认此译文。" });
    }
    var risky = column.status === "review_required" || column.is_refusal;
    el("editor-risk").className = "status-badge " + (risky ? "danger" : issues.length ? "warning" : "success");
    el("editor-risk").textContent = risky ? "必须复核" : column.status === "translated_needs_review" ? "建议复核" : issues.length ? "诊断提示" : "检查通过";
    el("editor-neighbors").innerHTML = (item.neighbors || []).length
        ? item.neighbors.map(function (neighbor) {
            return '<div class="neighbor"><span>' + (neighbor.position === "before" ? "前文" : "后文") + " · 第 " + (neighbor.row + 1) + " 行</span><p>" + escapeHtml(truncate(neighbor.original, 90)) + "</p>"
                + (neighbor.translated ? "<p>译：" + escapeHtml(truncate(neighbor.translated, 90)) + "</p>" : "") + "</div>";
        }).join("")
        : '<p class="hint">没有可用的相邻文本</p>';
    el("editor-issues").innerHTML = issues.length
        ? issues.map(function (issue) { return '<div class="issue-chip"><b>' + escapeHtml(issue.type || "issue") + "</b><br>" + escapeHtml(issue.message || issue.type) + "</div>"; }).join("")
        : '<p class="hint">自动检查未发现问题</p>';
    var terms = column.glossary_hits || [];
    el("editor-terms").innerHTML = terms.length
        ? terms.map(function (term) { return '<div class="term-chip">' + escapeHtml(term.source) + " → <b>" + escapeHtml(term.target) + "</b></div>"; }).join("")
        : '<p class="hint">没有命中已确认术语</p>';
    var meta = [
        ["模型", modelLabel(column.model_identifier) || "规则处理 / 未记录"],
        ["分类", column.entry_classification || "未记录"],
        ["批次", column.batch_id || "未记录"],
        ["重试", String(column.retry_count || 0)],
        ["更新时间", formatDate(column.updated_at)],
    ];
    if (column.ai_review && column.ai_review.status) {
        meta.push(["AI复核", { fixed: "已修复", confirmed: "确认原译文", unresolved: "仍无法确认", conflict: "写入冲突" }[column.ai_review.status] || column.ai_review.status]);
        meta.push(["复核模型", modelLabel(column.ai_review.review_model) || "未记录"]);
        meta.push(["验证模型", modelLabel(column.ai_review.verifier_model) || "未记录"]);
    }
    el("editor-generation").innerHTML = meta.map(function (entry) {
        return "<div><dt>" + escapeHtml(entry[0]) + "</dt><dd>" + escapeHtml(entry[1]) + "</dd></div>";
    }).join("");
    el("editor-save-note").textContent = "";
}

function statusLabel(status) {
    return {
        translated: "已翻译",
        translated_needs_review: "建议复核",
        review_required: "必须人工复核",
        preserved: "保留原文",
        pending: "尚未处理",
        failed_refusal: "模型拒答",
        failed_untranslated: "日文残留",
        done: "已确认",
    }[status] || status || "待复核";
}

async function saveReview(action) {
    if (reviewActionIsBusy() || aiReviewIsActive()) return;
    var row = state.review.selectedRow;
    if (row == null) return;
    var text = el("editor-target").value;
    if (action !== "preserve" && !text.trim()) {
        toast("译文不能为空", "error");
        return;
    }
    var buttonId = { accept: "btn-review-accept", draft: "btn-review-draft", preserve: "btn-review-preserve" }[action];
    setReviewActionBusy("save", buttonId);
    try {
        var result = await API.post("/review/save", {
            file_path: activeFilePath(), row: row, col: 0, text: text, action: action,
        });
        el("editor-save-note").textContent = action === "accept" ? "已保存并确认" : action === "draft" ? "已保存，仍保留在复核队列" : "已标记为保留原文";
        toast(el("editor-save-note").textContent, "success");
        state.hasUnexportedResult = true;
        state.exportReady = true;
        setReviewRowSelected(row, false);
        await refreshReviewAfterEdit();
        if (result.violations && result.violations.length) toast("保存后仍检测到 " + result.violations.length + " 个约束问题", "error");
    } catch (error) { toast(error.message, "error"); }
    finally { setReviewActionBusy(); }
}

async function acceptSelected() {
    if (reviewActionIsBusy() || aiReviewIsActive()) return;
    var edits = [];
    state.review.selectedItems.forEach(function (item, row) {
        if (!state.review.selectedRows.has(row)) return;
        var column = reviewColumn(item);
        edits.push({ row: row, col: 0, text: column.translated || "", action: "accept" });
    });
    if (!edits.length) return;
    setReviewActionBusy("batch_save", "btn-accept-selected");
    try {
        var result = await API.post("/review/batch-save", { file_path: activeFilePath(), edits: edits });
        toast("已确认 " + result.saved_count + " 个条目", "success");
        state.hasUnexportedResult = result.saved_count > 0 || state.hasUnexportedResult;
        state.exportReady = true;
        clearReviewSelection();
        await refreshReviewAfterEdit();
    } catch (error) { toast(error.message, "error"); }
    finally { setReviewActionBusy(); }
}

async function refreshReviewAfterEdit() {
    var stats = await API.get("/review/stats?file_path=" + encodeURIComponent(activeFilePath()));
    state.review.stats = stats;
    renderReviewStats(stats);
    await loadReviewList(state.review.offset);
}

async function jumpReview() {
    var row = Number(el("review-jump").value) - 1;
    if (!Number.isInteger(row) || row < 0) {
        toast("请输入有效行号", "error");
        return;
    }
    try {
        var result = await API.get(
            "/review/jump?file_path=" + encodeURIComponent(activeFilePath())
            + "&row=" + row + "&limit=" + state.review.limit
            + "&filter=" + encodeURIComponent(state.review.filter)
        );
        if (!result.found) {
            toast("该行不在当前筛选中", "error");
            return;
        }
        state.review.selectedRow = row;
        await loadReviewList(result.offset);
    } catch (error) { toast(error.message, "error"); }
}
