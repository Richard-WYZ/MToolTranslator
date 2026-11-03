"use strict";

var reviewDrafts = new Map();
var reviewDraftTimer = null;
var reviewDraftBridgeState = null;
var reviewDraftBridgeInFlight = false;

function reviewDraftKey(path, row) { return JSON.stringify([path, Number(row)]); }

function syncPendingReviewDraft() {
    var bridge = window.pywebview && window.pywebview.api;
    if (!bridge || typeof bridge.set_pending_review_draft !== "function") return;
    if (reviewDraftBridgeInFlight) return;
    var pending = reviewDrafts.size > 0;
    if (reviewDraftBridgeState === pending) return;
    reviewDraftBridgeState = pending;
    reviewDraftBridgeInFlight = true;
    try {
        Promise.resolve(bridge.set_pending_review_draft(pending)).then(function () {
            reviewDraftBridgeInFlight = false;
            // A fast save can clear the draft while the native "pending" push
            // is still running. Send the latest value only after it completes.
            syncPendingReviewDraft();
        }, function () {
            reviewDraftBridgeInFlight = false;
            reviewDraftBridgeState = null;
            if ((reviewDrafts.size > 0) !== pending) syncPendingReviewDraft();
        });
    } catch (error) { reviewDraftBridgeState = null; reviewDraftBridgeInFlight = false; }
}

function persistReviewDrafts() {
    try {
        sessionStorage.setItem("lgt.reviewDrafts", JSON.stringify(Array.from(reviewDrafts.values()).map(function (draft) {
            return { path: draft.path, row: draft.row, text: draft.text, original: draft.original };
        })));
    } catch (error) { /* The in-memory draft remains available when browser storage is full. */ }
    syncPendingReviewDraft();
}

function reviewSaveNote(message, failed) {
    el("editor-save-note").textContent = message || "";
    el("editor-save-note").className = "hint" + (failed ? " draft-save-error" : "");
}

function captureReviewDraft() {
    var path = state.review.editorFilePath, row = state.review.editorRow;
    if (!path || row == null || path !== activeFilePath() || el("review-editor").hidden) return null;
    var text = el("editor-target").value;
    var key = reviewDraftKey(path, row), draft = reviewDrafts.get(key);
    if (!draft && text === state.review.editorSavedText) return null;
    if (!draft) draft = { path: path, row: row, original: state.review.editorSource, version: 0 };
    if (draft.text !== text) { draft.text = text; draft.version += 1; draft.error = ""; }
    reviewDrafts.set(key, draft);
    persistReviewDrafts();
    return draft;
}

async function saveReviewDraftEntry(draft) {
    if (draft.saving) return draft.saving;
    draft.saving = (async function () {
        while (reviewDrafts.get(reviewDraftKey(draft.path, draft.row)) === draft) {
            var version = draft.version, text = draft.text;
            if (!text.trim()) {
                draft.error = "译文为空，尚未写回；请继续编辑。";
                if (state.review.editorFilePath === draft.path && state.review.editorRow === draft.row) reviewSaveNote(draft.error, true);
                return false;
            }
            if (state.review.editorFilePath === draft.path && state.review.editorRow === draft.row) reviewSaveNote("正在保存草稿…");
            try {
                var result = await API.post("/review/save", { file_path: draft.path, row: draft.row, col: 0, text: text, action: "draft" });
                var item = state.review.items.find(function (entry) { return entry.row === draft.row; });
                if (activeFilePath() === draft.path && item) {
                    var column = reviewColumn(item);
                    column.translated = result.text == null ? text : result.text;
                    column.status = "translated_needs_review";
                    column.manual_confirmed = false;
                }
                state.hasUnexportedResult = true;
                state.exportReady = true;
                if (state.review.editorFilePath === draft.path && state.review.editorRow === draft.row) state.review.editorSavedText = text;
                if (version !== draft.version) continue;
                reviewDrafts.delete(reviewDraftKey(draft.path, draft.row));
                persistReviewDrafts();
                if (state.review.editorFilePath === draft.path && state.review.editorRow === draft.row) {
                    reviewSaveNote("草稿已保存 · 尚未人工确认");
                    el("editor-status").textContent = "草稿 · 待人工确认";
                }
                return true;
            } catch (error) {
                draft.error = error.message || "保存失败";
                if (state.review.editorFilePath === draft.path && state.review.editorRow === draft.row) {
                    reviewSaveNote("草稿保存失败，编辑仍保留在本机。点击“保存草稿”重试：" + draft.error, true);
                }
                return false;
            }
        }
        return true;
    })();
    try { return await draft.saving; }
    finally { draft.saving = null; }
}

async function flushReviewDraft() {
    clearTimeout(reviewDraftTimer);
    captureReviewDraft();
    var pending = Array.from(reviewDrafts.values()).filter(function (draft) { return draft.path === activeFilePath(); });
    if (!pending.length) return true;
    if (aiReviewIsActive()) { reviewSaveNote("AI 检查期间暂不能保存；本地草稿已保留", true); return false; }
    for (var draft of pending) {
        if (!await saveReviewDraftEntry(draft)) return false;
    }
    return true;
}

function reviewHasPendingDraft() {
    captureReviewDraft();
    return reviewDrafts.size > 0;
}

function scheduleReviewDraft() {
    captureReviewDraft();
    reviewSaveNote("编辑已保留，稍后自动保存草稿…");
    clearTimeout(reviewDraftTimer);
    reviewDraftTimer = setTimeout(function () { flushReviewDraft(); }, 700);
}

function reviewSearchParams() {
    return "&query=" + encodeURIComponent(state.review.query || "")
        + "&scope=" + encodeURIComponent(state.review.searchScope || "both");
}

function reviewHighlighted(text, field) {
    var value = String(text == null ? "" : text), query = String(state.review.query || "");
    var scope = state.review.searchScope || "both";
    if (!query || (scope !== "both" && scope !== field)) return escapeHtml(value);
    var lowered = value.toLocaleLowerCase(), needle = query.toLocaleLowerCase(), position = 0, result = "", index;
    while ((index = lowered.indexOf(needle, position)) !== -1) {
        result += escapeHtml(value.slice(position, index)) + "<mark>" + escapeHtml(value.slice(index, index + query.length)) + "</mark>";
        position = index + query.length;
    }
    return result + escapeHtml(value.slice(position));
}

function reviewQueueSnippet(value, field) {
    var text = String(value || ""), query = String(state.review.query || ""), scope = state.review.searchScope || "both";
    var index = query && (scope === "both" || scope === field) ? text.toLocaleLowerCase().indexOf(query.toLocaleLowerCase()) : -1;
    var start = index > 24 ? index - 24 : 0;
    return (start ? "…" : "") + reviewHighlighted(text.slice(start, start + Math.max(90, query.length + 48)), field)
        + (text.length > start + Math.max(90, query.length + 48) ? "…" : "");
}

async function setReviewFilter(filter) {
    if (!await flushReviewDraft()) return;
    state.review.filter = filter || "all";
    state.review.selectedRow = null;
    all("#review-filters [data-filter]").forEach(function (node) { node.classList.toggle("active", node.dataset.filter === state.review.filter); });
    try { await loadReviewList(0); }
    catch (error) { toast(error.message, "error"); }
}

async function openReviewSearch(options) {
    options = options || {};
    if (!await flushReviewDraft()) return;
    state.review.query = String(options.query == null ? el("review-search").value : options.query).trim();
    state.review.searchScope = options.scope || el("review-search-scope").value || "both";
    if (options.filter) state.review.filter = options.filter;
    state.review.selectedRow = null;
    el("review-search").value = state.review.query;
    el("review-search-scope").value = state.review.searchScope;
    all("#review-filters [data-filter]").forEach(function (node) { node.classList.toggle("active", node.dataset.filter === state.review.filter); });
    if (state.page !== "review") { navigate("review"); return; }
    try { await loadReviewList(0); }
    catch (error) { toast(error.message, "error"); }
}

async function moveReviewEntry(direction) {
    if (reviewActionIsBusy() || !await flushReviewDraft()) return;
    var index = state.review.items.findIndex(function (item) { return item.row === state.review.selectedRow; });
    var next = index + direction;
    if (next >= 0 && next < state.review.items.length) { await selectReviewRow(state.review.items[next].row); return; }
    var offset = state.review.offset + (direction > 0 ? state.review.limit : -state.review.limit);
    if (offset < 0 || offset >= state.review.total) return;
    state.review.selectedRow = null;
    await loadReviewList(offset);
    if (direction < 0 && state.review.items.length) await selectReviewRow(state.review.items[state.review.items.length - 1].row);
}

function initReviewWorkspace() {
    try {
        var cached = JSON.parse(sessionStorage.getItem("lgt.reviewDrafts") || "[]");
        if (Array.isArray(cached)) cached.forEach(function (draft) {
            if (typeof draft.path === "string" && Number.isInteger(draft.row) && typeof draft.text === "string") {
                reviewDrafts.set(reviewDraftKey(draft.path, draft.row), Object.assign(draft, { version: 1 }));
            }
        });
    } catch (error) { /* Ignore an invalid local draft cache. */ }
    syncPendingReviewDraft();
    window.addEventListener("pywebviewready", function () {
        reviewDraftBridgeState = null;
        syncPendingReviewDraft();
    });
    el("review-search-form").addEventListener("submit", function (event) { event.preventDefault(); openReviewSearch(); });
    el("review-search-scope").addEventListener("change", function () { openReviewSearch(); });
    el("btn-review-search-clear").addEventListener("click", function () { openReviewSearch({ query: "" }); });
    el("review-search").addEventListener("search", function () { if (!this.value) openReviewSearch({ query: "" }); });
    el("editor-target").addEventListener("input", scheduleReviewDraft);
    el("btn-ai-sensitive-test").addEventListener("click", testAIReviewSensitiveModel);
    el("editor-target").addEventListener("blur", function () { flushReviewDraft(); });
    el("btn-review-entry-prev").addEventListener("click", function () { moveReviewEntry(-1); });
    el("btn-review-entry-next").addEventListener("click", function () { moveReviewEntry(1); });
    el("editor-neighbors").addEventListener("click", async function (event) {
        var button = event.target.closest("[data-review-neighbor]");
        if (!button || !await flushReviewDraft()) return;
        await openReviewSearch({ query: "", filter: "all" });
        await jumpToReviewRow(Number(button.dataset.reviewNeighbor));
    });
    document.addEventListener("keydown", function (event) {
        if (state.page !== "review" || reviewActionIsBusy()) return;
        if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && event.target.closest("#review-editor")) {
            event.preventDefault(); saveReview("accept");
        }
        else if (event.altKey && ["ArrowLeft", "ArrowRight"].includes(event.key)) {
            event.preventDefault(); moveReviewEntry(event.key === "ArrowLeft" ? -1 : 1);
        }
    });
    window.addEventListener("beforeunload", function (event) {
        captureReviewDraft();
        if (reviewDrafts.size) { event.preventDefault(); event.returnValue = ""; }
    });
    window.openReviewSearch = openReviewSearch;
}

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
        ai_test: "正在测试成人内容兼容性…",
        save: "正在保存当前译文…",
        batch_save: "正在确认所选译文…",
        export: "正在导出当前结果…",
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
    if (!await flushReviewDraft()) return;
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
    el("review-count-translated").textContent = Number(stats.confirmed_translation || 0).toLocaleString("zh-CN");
    el("nav-review-count").hidden = !stats.required_review;
    el("nav-review-count").textContent = stats.required_review || 0;
}

function populateAIReviewModels() {
    var savedAutoRetry = localStorage.getItem("lgt.aiAutoRetry");
    if (savedAutoRetry !== null) el("ai-auto-retry").checked = savedAutoRetry !== "0";
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
    if (!await flushReviewDraft()) return;
    state.review.offset = Math.max(0, Number(offset || 0));
    var requestedOffset = state.review.offset;
    var requestedFilter = state.review.filter;
    var requestedQuery = state.review.query || "", requestedScope = state.review.searchScope || "both";
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
            + "&filter=" + encodeURIComponent(requestedFilter) + reviewSearchParams(),
            { signal: controller.signal }
        );
    } catch (error) {
        if (error && error.name === "AbortError") return;
        throw error;
    } finally {
        if (requestId === state.review.listRequestId) state.review.listController = null;
    }
    if (requestId !== state.review.listRequestId || path !== activeFilePath() || requestedFilter !== state.review.filter
        || requestedQuery !== (state.review.query || "") || requestedScope !== (state.review.searchScope || "both")) return;
    state.review.items = payload.items || [];
    state.review.items.forEach(function (item) {
        if (state.review.selectedRows.has(item.row)) state.review.selectedItems.set(item.row, item);
    });
    state.review.total = payload.matched_total || 0;
    state.review.offset = payload.offset || 0;
    renderReviewQueue();
    if (state.review.items.length) {
        var selected = state.review.items.find(function (item) { return item.row === state.review.selectedRow; }) || state.review.items[0];
        await selectReviewRow(selected.row);
    } else {
        state.review.selectedRow = null;
        state.review.editorRow = null;
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
    el("review-search-count").textContent = (state.review.query ? "全文搜索命中 " : "当前筛选 ")
        + state.review.total.toLocaleString("zh-CN") + " 条";
    if (!state.review.items.length) {
        queue.innerHTML = '<div class="empty-state compact">当前筛选下没有条目</div>';
    } else {
        queue.innerHTML = state.review.items.map(function (item) {
            var column = reviewColumn(item);
            var risk = column.is_refusal || column.status === "review_required" ? "danger" : "";
            var checked = state.review.selectedRows.has(item.row) ? " checked" : "";
            var selectable = reviewItemIsSelectable(item) && !reviewActionIsBusy() && !aiReviewIsActive();
            var aiStatus = (column.ai_review || {}).status || "";
            var aiLabel = { reclassified: "系统已校正", fixed: "AI 已修复", confirmed: "AI 检查通过", unresolved: "AI 未解决", conflict: "AI 冲突" }[aiStatus] || "";
            return '<div class="queue-item' + (item.row === state.review.selectedRow ? " selected" : "") + '" data-review-row="' + item.row + '">'
                + '<input type="checkbox" data-review-check="' + item.row + '"' + checked + (selectable ? "" : " disabled") + ' aria-label="选择第 ' + (item.row + 1) + ' 行">'
                + '<div class="queue-copy"><div class="queue-meta"><span><i class="risk-dot ' + risk + '"></i>第 ' + (item.row + 1) + ' 行' + (aiLabel ? '<i class="queue-ai-badge">' + aiLabel + "</i>" : "") + '</span><span>' + escapeHtml(column.manual_confirmed ? "人工已确认" : statusLabel(column.status)) + "</span></div>"
                + "<p>" + reviewQueueSnippet(column.original, "source") + "</p>"
                + '<p class="target">' + (reviewQueueSnippet(column.translated, "target") || (column.status === "pending" ? "翻译中 / 尚未处理" : "尚无译文")) + "</p></div></div>";
        }).join("");
    }
    var page = Math.floor(state.review.offset / state.review.limit) + 1;
    var pages = Math.max(1, Math.ceil(state.review.total / state.review.limit));
    el("review-page-label").textContent = page + " / " + pages;
    el("btn-review-prev").disabled = state.review.offset <= 0;
    el("btn-review-next").disabled = state.review.offset + state.review.limit >= state.review.total;
    renderReviewSelectionControls();
}

async function selectReviewRow(row) {
    var item = state.review.items.find(function (entry) { return entry.row === Number(row); });
    if (!item) return;
    var selectionRequest = (state.review.selectionRequest || 0) + 1;
    state.review.selectionRequest = selectionRequest;
    if (!await flushReviewDraft() || selectionRequest !== state.review.selectionRequest) return;
    state.review.selectedRow = item.row;
    all(".queue-item").forEach(function (node) { node.classList.toggle("selected", Number(node.dataset.reviewRow) === item.row); });
    renderReviewEditor(item);
}

function renderReviewEditor(item) {
    var column = reviewColumn(item);
    el("review-editor-empty").hidden = true;
    el("review-editor").hidden = false;
    el("editor-row").textContent = "第 " + (item.row + 1) + " 行";
    el("editor-status").textContent = column.manual_confirmed ? "人工已确认" : statusLabel(column.status);
    el("editor-source").innerHTML = reviewHighlighted(column.original || "", "source");
    state.review.editorFilePath = activeFilePath();
    state.review.editorRow = item.row;
    state.review.editorSavedText = column.translated || "";
    state.review.editorSource = column.original || "";
    var draft = reviewDrafts.get(reviewDraftKey(activeFilePath(), item.row));
    el("editor-target").value = draft ? draft.text : column.translated || "";
    var showTargetMatches = Boolean(state.review.query) && ["both", "target"].includes(state.review.searchScope || "both");
    el("editor-target-matches").hidden = !showTargetMatches;
    el("editor-target-matches").innerHTML = showTargetMatches ? reviewHighlighted(el("editor-target").value, "target") : "";
    var index = state.review.items.findIndex(function (entry) { return entry.row === item.row; });
    var position = state.review.offset + index;
    el("review-entry-position").textContent = (position + 1).toLocaleString("zh-CN") + " / " + state.review.total.toLocaleString("zh-CN")
        + (state.review.query ? " 条搜索结果" : " 条");
    el("btn-review-entry-prev").disabled = position <= 0;
    el("btn-review-entry-next").disabled = position + 1 >= state.review.total;
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
    el("editor-risk").textContent = column.manual_confirmed ? "人工已确认" : risky ? "需要处理" : column.status === "translated_needs_review" ? "建议复核" : issues.length ? "诊断提示" : "机器检查通过";
    el("editor-neighbors").innerHTML = (item.neighbors || []).length
        ? item.neighbors.map(function (neighbor) {
            return '<details class="neighbor"><summary>' + (neighbor.position === "before" ? "前文" : "后文") + " · 第 " + (neighbor.row + 1) + ' 行</summary><p>' + reviewHighlighted(neighbor.original, "source") + "</p>"
                + (neighbor.translated ? "<p>译：" + reviewHighlighted(neighbor.translated, "target") + "</p>" : "")
                + '<button class="btn btn-ghost btn-sm" data-review-neighbor="' + neighbor.row + '">定位此条</button></details>';
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
        meta.push(["AI复核", { reclassified: "系统确定性校正", fixed: "已修复", confirmed: "确认原译文", unresolved: "仍无法确认", conflict: "写入冲突" }[column.ai_review.status] || column.ai_review.status]);
        meta.push(["复核模型", modelLabel(column.ai_review.review_model) || "未记录"]);
        meta.push(["验证模型", modelLabel(column.ai_review.verifier_model) || "未记录"]);
    }
    el("editor-generation").innerHTML = meta.map(function (entry) {
        return "<div><dt>" + escapeHtml(entry[0]) + "</dt><dd>" + escapeHtml(entry[1]) + "</dd></div>";
    }).join("");
    reviewSaveNote(draft ? "有尚未写回的本地草稿" + (draft.error ? "：" + draft.error : "") : column.manual_confirmed ? "此译文已由人工确认" : "修改后自动保存草稿", Boolean(draft && draft.error));
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
    if (!await flushReviewDraft()) return;
    var row = state.review.selectedRow;
    if (row == null) return;
    var text = el("editor-target").value;
    if (action !== "preserve" && !text.trim()) {
        toast("译文不能为空", "error");
        return;
    }
    var buttonId = { accept: "btn-review-accept", draft: "btn-review-draft", preserve: "btn-review-preserve" }[action];
    var index = state.review.items.findIndex(function (item) { return item.row === row; });
    var nextRow = action === "accept" && state.review.items[index + 1] ? state.review.items[index + 1].row : null;
    var requestedPath = activeFilePath();
    setReviewActionBusy("save", buttonId);
    try {
        var result = await API.post("/review/save", {
            file_path: requestedPath, row: row, col: 0, text: text, action: action,
        });
        if (requestedPath !== activeFilePath()) return;
        state.review.editorSavedText = result.text == null ? text : result.text;
        el("editor-target").value = state.review.editorSavedText;
        reviewDrafts.delete(reviewDraftKey(requestedPath, row));
        persistReviewDrafts();
        el("editor-save-note").textContent = action === "accept" ? "人工确认已保存" : action === "draft" ? "草稿已保存，尚未人工确认" : "已标记为保留原文";
        toast(el("editor-save-note").textContent, "success");
        state.hasUnexportedResult = true;
        state.exportReady = true;
        setReviewRowSelected(row, false);
        if (nextRow != null) state.review.selectedRow = nextRow;
        else if (action === "accept") state.review.selectedRow = row;
        await refreshReviewAfterEdit();
        if (action === "accept" && nextRow == null && state.review.items.some(function (item) { return item.row === row; })
            && state.review.offset + state.review.items.length < state.review.total) {
            await loadReviewList(state.review.offset + state.review.limit);
        }
        else if (action === "accept" && nextRow == null && state.review.items.length
            && !state.review.items.some(function (item) { return item.row === row; })) {
            await selectReviewRow(state.review.items[Math.min(index, state.review.items.length - 1)].row);
        }
        if (result.violations && result.violations.length) toast("保存后仍检测到 " + result.violations.length + " 个约束问题", "error");
    } catch (error) { toast(error.message, "error"); }
    finally { setReviewActionBusy(); }
}

async function acceptSelected() {
    if (reviewActionIsBusy() || aiReviewIsActive()) return;
    if (!await flushReviewDraft()) return;
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
    await jumpToReviewRow(row);
}

async function jumpToReviewRow(row) {
    if (!await flushReviewDraft()) return;
    try {
        var result = await API.get(
            "/review/jump?file_path=" + encodeURIComponent(activeFilePath())
            + "&row=" + row + "&limit=" + state.review.limit
            + "&filter=" + encodeURIComponent(state.review.filter) + reviewSearchParams()
        );
        if (!result.found) {
            toast("该行不在当前筛选中", "error");
            return;
        }
        state.review.selectedRow = row;
        await loadReviewList(result.offset);
    } catch (error) { toast(error.message, "error"); }
}
