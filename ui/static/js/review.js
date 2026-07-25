"use strict";

async function refreshReviewCount() {
    if (!activeFilePath()) return;
    try {
        var stats = await API.get("/review/stats?file_path=" + encodeURIComponent(activeFilePath()));
        var count = Number(stats.needs_review || 0);
        el("nav-review-count").hidden = count === 0;
        el("nav-review-count").textContent = count;
    } catch (error) {
        // 尚无翻译输出时无需提示。
    }
}

async function loadReview() {
    var path = activeFilePath();
    if (!path) {
        el("review-empty").hidden = false;
        el("review-workspace").hidden = true;
        return;
    }
    try {
        var stats = await API.get("/review/stats?file_path=" + encodeURIComponent(path));
        state.review.stats = stats;
        el("review-empty").hidden = false;
        el("review-workspace").hidden = true;
        if (!stats.total) return;
        el("review-empty").hidden = true;
        el("review-workspace").hidden = false;
        renderReviewStats(stats);
        await loadReviewList(state.review.offset);
    } catch (error) {
        el("review-empty").hidden = false;
        el("review-workspace").hidden = true;
        toast(error.message, "error");
    }
}

function renderReviewStats(stats) {
    el("review-stat-total").textContent = Number(stats.total || 0).toLocaleString("zh-CN");
    el("review-stat-needs").textContent = Number(stats.needs_review || 0).toLocaleString("zh-CN");
    el("review-stat-issues").textContent = Number(stats.violations_count || 0).toLocaleString("zh-CN");
    el("review-stat-done").textContent = Number(stats.reviewed || 0).toLocaleString("zh-CN");
    el("nav-review-count").hidden = !stats.needs_review;
    el("nav-review-count").textContent = stats.needs_review || 0;
}

async function loadReviewList(offset) {
    var path = activeFilePath();
    if (!path) return;
    state.review.offset = Math.max(0, Number(offset || 0));
    var payload = await API.get(
        "/review/list?file_path=" + encodeURIComponent(path)
        + "&offset=" + state.review.offset
        + "&limit=" + state.review.limit
        + "&filter=" + encodeURIComponent(state.review.filter)
    );
    state.review.items = payload.items || [];
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
            return '<div class="queue-item' + (item.row === state.review.selectedRow ? " selected" : "") + '" data-review-row="' + item.row + '">'
                + '<input type="checkbox" data-review-check="' + item.row + '"' + checked + ' aria-label="选择第 ' + (item.row + 1) + ' 行">'
                + '<div class="queue-copy"><div class="queue-meta"><span><i class="risk-dot ' + risk + '"></i>第 ' + (item.row + 1) + ' 行</span><span>' + escapeHtml(column.status || "pending") + "</span></div>"
                + "<p>" + escapeHtml(truncate(column.original, 42)) + "</p>"
                + '<p class="target">' + escapeHtml(truncate(column.translated, 42) || "尚无译文") + "</p></div></div>";
        }).join("");
    }
    var page = Math.floor(state.review.offset / state.review.limit) + 1;
    var pages = Math.max(1, Math.ceil(state.review.total / state.review.limit));
    el("review-page-label").textContent = page + " / " + pages;
    el("btn-review-prev").disabled = state.review.offset <= 0;
    el("btn-review-next").disabled = state.review.offset + state.review.limit >= state.review.total;
    el("btn-accept-selected").disabled = state.review.selectedRows.size === 0;
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
    el("editor-risk").textContent = risky ? "必须复核" : issues.length ? "需要复核" : "检查通过";
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
    el("editor-generation").innerHTML = meta.map(function (entry) {
        return "<div><dt>" + escapeHtml(entry[0]) + "</dt><dd>" + escapeHtml(entry[1]) + "</dd></div>";
    }).join("");
    el("editor-save-note").textContent = "";
}

function statusLabel(status) {
    return {
        translated: "已翻译",
        translated_needs_review: "译文待复核",
        review_required: "必须人工复核",
        preserved: "保留原文",
        pending: "尚未处理",
        failed_refusal: "模型拒答",
        failed_untranslated: "日文残留",
        done: "已确认",
    }[status] || status || "待复核";
}

async function saveReview(action) {
    var row = state.review.selectedRow;
    if (row == null) return;
    var text = el("editor-target").value;
    if (action !== "preserve" && !text.trim()) {
        toast("译文不能为空", "error");
        return;
    }
    try {
        var result = await API.post("/review/save", {
            file_path: activeFilePath(), row: row, col: 0, text: text, action: action,
        });
        el("editor-save-note").textContent = action === "accept" ? "已保存并确认" : action === "draft" ? "已保存，仍保留在复核队列" : "已标记为保留原文";
        toast(el("editor-save-note").textContent, "success");
        await refreshReviewAfterEdit();
        if (result.violations && result.violations.length) toast("保存后仍检测到 " + result.violations.length + " 个约束问题", "error");
    } catch (error) { toast(error.message, "error"); }
}

async function acceptSelected() {
    var edits = [];
    state.review.items.forEach(function (item) {
        if (!state.review.selectedRows.has(item.row)) return;
        var column = reviewColumn(item);
        edits.push({ row: item.row, col: 0, text: column.translated || "", action: "accept" });
    });
    if (!edits.length) return;
    try {
        var result = await API.post("/review/batch-save", { file_path: activeFilePath(), edits: edits });
        toast("已确认 " + result.saved_count + " 个条目", "success");
        state.review.selectedRows.clear();
        await refreshReviewAfterEdit();
    } catch (error) { toast(error.message, "error"); }
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
