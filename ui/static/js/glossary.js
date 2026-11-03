"use strict";

async function loadGlossary() {
    var path = activeFilePath();
    if (state.glossary.detailFile && state.glossary.detailFile !== path) {
        state.glossary.detailSource = "";
        state.glossary.detailDirty = false;
        state.glossary.detailBusy = false;
        state.glossary.detailRequest = (state.glossary.detailRequest || 0) + 1;
        el("glossary-detail").hidden = true;
    }
    if (!activeFilePath()) {
        el("glossary-empty-file").hidden = false;
        el("glossary-workspace").hidden = true;
        return;
    }
    try {
        var payload = await API.get("/glossary/dynamic?file_path=" + encodeURIComponent(path));
        if (path !== activeFilePath()) return;
        state.glossary.payload = payload;
        el("glossary-empty-file").hidden = true;
        el("glossary-workspace").hidden = false;
        el("glossary-file-label").textContent = state.fileName || activeFilePath();
        renderGlossary();
    } catch (error) { toast(error.message, "error"); }
}

function glossaryStatusLabel(status) {
    return { candidate: "待确认译法", confirmed: "已确认，会统一使用", official: "官方译法", needs_review: "译法有疑问", rejected: "不采用" }[status] || "待确认译法";
}

function glossaryEvidenceLabel(value) {
    return {
        manual_confirmed: "由你确认", whole_file_preseed: "在文件中多次出现", speaker_position: "出现在说话人位置",
        standalone_line: "作为独立名称出现", quoted_name: "出现在引号中的名称", person_like: "具有姓名特征",
        katakana: "片假名名称", katakana_name: "片假名名称", kanji_name: "汉字姓名", repeated: "多次出现",
        preflight_name_conflict: "不同判断存在分歧", preflight_person_confirmed: "姓名判断已核实",
        title_suffix: "与称呼一起出现", name_suffix: "带有姓名或称呼特征", known_name: "已识别名称",
    }[value] || "文件中的名称线索";
}

function candidateTarget(info) {
    var targets = (info && info.targets) || {};
    if (Array.isArray(targets)) return targets[0] || "";
    var names = Object.keys(targets);
    names.sort(function (a, b) {
        var av = typeof targets[a] === "number" ? targets[a] : (targets[a] && targets[a].count) || 0;
        var bv = typeof targets[b] === "number" ? targets[b] : (targets[b] && targets[b].count) || 0;
        return bv - av;
    });
    return names[0] || info.target || "";
}

function glossaryRows() {
    var terms = state.glossary.payload.terms || {};
    var candidates = state.glossary.payload.candidates || {};
    var sources = Array.from(new Set(Object.keys(terms).concat(Object.keys(candidates))));
    return sources.map(function (source) {
        var info = candidates[source] || {};
        var confirmed = Object.prototype.hasOwnProperty.call(terms, source);
        return {
            source: source,
            target: confirmed ? terms[source] : candidateTarget(info),
            status: confirmed ? (info.status === "official" ? "official" : "confirmed") : (info.status || "candidate"),
            evidence: info.evidence || [],
            info: info,
            confirmed: confirmed,
        };
    });
}

function renderGlossary() {
    var rows = glossaryRows();
    var counts = {
        all: rows.length,
        confirmed: rows.filter(function (row) { return ["confirmed", "official"].includes(row.status); }).length,
        candidate: rows.filter(function (row) { return row.status === "candidate"; }).length,
        needs_review: rows.filter(function (row) { return row.status === "needs_review"; }).length,
    };
    el("glossary-count-all").textContent = counts.all;
    el("glossary-count-confirmed").textContent = counts.confirmed;
    el("glossary-count-candidate").textContent = counts.candidate;
    el("glossary-count-review").textContent = counts.needs_review;
    var query = state.glossary.search.toLowerCase();
    rows = rows.filter(function (row) {
        var filterMatch = state.glossary.filter === "all"
            || state.glossary.filter === "confirmed" && ["confirmed", "official"].includes(row.status)
            || row.status === state.glossary.filter;
        var searchMatch = !query || (row.source + " " + row.target).toLowerCase().includes(query);
        return filterMatch && searchMatch;
    });
    el("glossary-empty").hidden = rows.length !== 0;
    el("glossary-body").innerHTML = rows.map(function (row) {
        var evidence = Array.isArray(row.evidence) ? Array.from(new Set(row.evidence.map(glossaryEvidenceLabel))).join(" · ") : "文件中的名称线索";
        var actions = row.confirmed
            ? '<button class="btn btn-secondary btn-sm" data-term-edit="' + escapeHtml(row.source) + '">编辑</button><button class="btn btn-ghost btn-sm" data-term-delete="' + escapeHtml(row.source) + '">移除</button>'
            : '<button class="btn btn-primary btn-sm" data-term-promote="' + escapeHtml(row.source) + '">确认译法</button><button class="btn btn-ghost btn-sm" data-term-delete="' + escapeHtml(row.source) + '">不采用</button>';
        return '<tr><td><button type="button" class="btn btn-ghost btn-sm" data-term-details="' + escapeHtml(row.source) + '">' + escapeHtml(row.source) + '</button></td><td>' + escapeHtml(row.target || "尚无稳定译法") + '</td><td><span class="glossary-status ' + escapeHtml(row.status) + '">' + escapeHtml(glossaryStatusLabel(row.status)) + "</span></td><td>" + escapeHtml(evidence || "—") + '</td><td><div class="term-actions">' + '<button type="button" class="btn btn-ghost btn-sm" data-term-details="' + escapeHtml(row.source) + '">查看用例</button>' + actions + "</div></td></tr>";
    }).join("");
}

async function editGlossaryTerm(source) {
    return openGlossaryDetail(source);
}

async function promoteGlossaryTerm(source) {
    return openGlossaryDetail(source);
}

async function deleteGlossaryTerm(source) {
    if (state.glossary.detailBusy) return;
    if (!window.confirm("移除术语“" + source + "”？已生成译文不会被自动回滚。")) return;
    try {
        await API.delete("/glossary/dynamic/" + encodeURIComponent(source) + "?file_path=" + encodeURIComponent(activeFilePath()));
        toast("术语已移除", "success");
        if (state.glossary.detailSource === source) closeGlossaryDetail(true);
        await loadGlossary();
    } catch (error) { toast(error.message, "error"); }
}

function glossaryHasUnsavedChanges() {
    return Boolean(state.glossary.detailDirty);
}

function confirmGlossaryLeave() {
    return !glossaryHasUnsavedChanges() || window.confirm("术语译法尚未应用，放弃这次修改吗？");
}

function closeGlossaryDetail(force) {
    if (state.glossary.detailBusy || (!force && !confirmGlossaryLeave())) return;
    state.glossary.detailSource = "";
    state.glossary.detailDirty = false;
    state.glossary.detailRequest = (state.glossary.detailRequest || 0) + 1;
    el("glossary-detail").hidden = true;
}

async function openGlossaryDetail(source) {
    if (state.glossary.detailBusy || !confirmGlossaryLeave()) return;
    var row = glossaryRows().find(function (item) { return item.source === source; });
    if (!row) return;
    state.glossary.detailFile = activeFilePath();
    state.glossary.detailSource = source;
    state.glossary.detailConfirmed = row.confirmed;
    state.glossary.detailInitialTarget = row.target || "";
    state.glossary.detailDirty = false;
    state.glossary.detailPreview = null;
    el("glossary-detail").hidden = false;
    el("glossary-detail-title").textContent = source + " · " + glossaryStatusLabel(row.status);
    el("glossary-detail-target").value = row.target || "";
    el("btn-glossary-apply").textContent = row.confirmed ? "更新并检查译文" : "确认并应用";
    await previewGlossaryImpact(0);
    if (el("glossary-detail").scrollIntoView) el("glossary-detail").scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function updateGlossaryDetailControls() {
    var busy = Boolean(state.glossary.detailBusy);
    var preview = state.glossary.detailPreview;
    var target = el("glossary-detail-target").value.trim();
    el("glossary-detail").setAttribute("aria-busy", busy ? "true" : "false");
    el("glossary-detail-target").disabled = busy;
    el("btn-glossary-preview").disabled = busy || !target;
    el("btn-glossary-apply").disabled = busy || !target || !preview || preview.target !== target || !preview.target_valid;
    el("btn-glossary-close").disabled = busy;
    el("btn-glossary-related").disabled = busy;
    el("btn-glossary-prev").disabled = busy || !preview || preview.offset <= 0;
    el("btn-glossary-next").disabled = busy || !preview || preview.offset + preview.limit >= preview.entry_count;
}

async function previewGlossaryImpact(offset) {
    var source = state.glossary.detailSource, file = state.glossary.detailFile;
    if (!source || file !== activeFilePath() || state.glossary.detailBusy) return;
    var requestId = (state.glossary.detailRequest || 0) + 1;
    state.glossary.detailRequest = requestId;
    var target = el("glossary-detail-target").value.trim();
    state.glossary.detailBusy = true;
    el("glossary-detail-note").textContent = "正在读取全文中的出现位置…";
    updateGlossaryDetailControls();
    try {
        var result = await API.get("/glossary/occurrences?file_path=" + encodeURIComponent(file)
            + "&source=" + encodeURIComponent(source) + "&target=" + encodeURIComponent(target)
            + "&offset=" + Math.max(0, offset || 0) + "&limit=10");
        if (requestId !== state.glossary.detailRequest || file !== activeFilePath()) return;
        state.glossary.detailPreview = result;
        el("glossary-detail-summary").textContent = "出现 " + result.occurrence_count + " 次，分布在 " + result.entry_count + " 条原文中；" + result.affected_count + " 条已有译文需要检查";
        el("glossary-detail-note").textContent = !target ? "填写统一译法后预览影响。"
            : result.target_valid ? "预览只读取当前结果；确认应用后才会更新术语与相关译文。" : "该译法未通过术语检查，请核对后修改。";
        renderGlossaryOccurrences(result);
    } catch (error) {
        if (requestId === state.glossary.detailRequest) {
            state.glossary.detailPreview = null;
            el("glossary-detail-note").textContent = error.message;
            toast(error.message, "error");
        }
    } finally {
        if (requestId === state.glossary.detailRequest) state.glossary.detailBusy = false;
        updateGlossaryDetailControls();
    }
}

function renderGlossaryOccurrences(result) {
    var effects = { preserved: "已保留原文，不自动替换", derived: "组合文本，随相关片段重新生成", pending: "待翻译时使用确认译法", replace: "整条名称可直接统一，之后重新检查", revalidate: "修改后重新检查，必要时重译" };
    el("glossary-occurrences").innerHTML = result.items.length ? result.items.map(function (item) {
        return '<article class="glossary-example"><div><strong>第 ' + (item.row + 1) + ' 条</strong><span>' + escapeHtml(effects[item.effect] || "相关译文") + '</span></div>'
            + '<div class="glossary-example-pair"><section><label>原文</label><p>' + escapeHtml(item.original) + '</p></section><section><label>当前译文</label><p>' + escapeHtml(item.translated || "尚无译文") + '</p></section></div>'
            + (item.preview_translation !== item.translated ? '<p class="hint">拟统一为：' + escapeHtml(item.preview_translation) + '</p>' : "") + '</article>';
    }).join("") : '<p class="hint">当前原文中没有匹配的术语用例。</p>';
    el("glossary-occurrence-page").textContent = result.entry_count ? (Math.floor(result.offset / result.limit) + 1) + " / " + Math.ceil(result.entry_count / result.limit) : "0 / 0";
}

async function applyGlossaryDetail(event) {
    event.preventDefault();
    var preview = state.glossary.detailPreview;
    var source = state.glossary.detailSource, file = state.glossary.detailFile;
    var target = el("glossary-detail-target").value.trim();
    if (state.glossary.detailBusy || !preview || !preview.target_valid || preview.target !== target || file !== activeFilePath()) return;
    state.glossary.detailBusy = true;
    updateGlossaryDetailControls();
    try {
        var payload = { file_path: file, source: source, target: target };
        var result = state.glossary.detailConfirmed
            ? await API.put("/glossary/dynamic/" + encodeURIComponent(source), payload)
            : await API.post("/glossary/promote", payload);
        if (file !== activeFilePath()) return;
        state.glossary.detailDirty = false;
        state.glossary.detailInitialTarget = target;
        state.glossary.detailConfirmed = true;
        el("glossary-detail-title").textContent = source + " · 已确认，会统一使用";
        el("btn-glossary-apply").textContent = "更新并检查译文";
        state.hasUnexportedResult = true;
        el("glossary-detail-note").textContent = "已应用，检查了 " + Number(result.updated_cells || 0) + " 条相关译文；可打开相关译文继续检查。";
        toast(el("glossary-detail-note").textContent, "success");
        await loadGlossary();
        await refreshReviewCount();
        if (typeof refreshExportState === "function") await refreshExportState();
    } catch (error) { toast(error.message, "error"); }
    finally { state.glossary.detailBusy = false; updateGlossaryDetailControls(); }
}

function initGlossaryWorkspace() {
    if (state.glossary.workspaceBound) return;
    state.glossary.workspaceBound = true;
    el("glossary-body").addEventListener("click", function (event) {
        var button = event.target.closest("[data-term-details]");
        if (button) openGlossaryDetail(button.dataset.termDetails);
    });
    el("btn-glossary-close").addEventListener("click", function () { closeGlossaryDetail(false); });
    el("glossary-detail-target").addEventListener("input", function () {
        state.glossary.detailDirty = this.value !== state.glossary.detailInitialTarget;
        state.glossary.detailPreview = null;
        el("glossary-detail-note").textContent = "译法已修改，请重新预览影响。";
        updateGlossaryDetailControls();
    });
    el("btn-glossary-preview").addEventListener("click", function () { previewGlossaryImpact(0); });
    el("glossary-term-form").addEventListener("submit", applyGlossaryDetail);
    el("btn-glossary-prev").addEventListener("click", function () { var p = state.glossary.detailPreview; if (p) previewGlossaryImpact(p.offset - p.limit); });
    el("btn-glossary-next").addEventListener("click", function () { var p = state.glossary.detailPreview; if (p) previewGlossaryImpact(p.offset + p.limit); });
    el("btn-glossary-related").addEventListener("click", function () {
        if (!confirmGlossaryLeave()) return;
        state.glossary.detailDirty = false;
        if (typeof window.openReviewSearch === "function") window.openReviewSearch({ query: state.glossary.detailSource, scope: "source", filter: "all" });
    });
}
