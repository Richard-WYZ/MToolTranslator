"use strict";

async function loadGlossary() {
    if (!activeFilePath()) {
        el("glossary-empty-file").hidden = false;
        el("glossary-workspace").hidden = true;
        return;
    }
    try {
        state.glossary.payload = await API.get("/glossary/dynamic?file_path=" + encodeURIComponent(activeFilePath()));
        el("glossary-empty-file").hidden = true;
        el("glossary-workspace").hidden = false;
        el("glossary-file-label").textContent = state.fileName || activeFilePath();
        renderGlossary();
    } catch (error) { toast(error.message, "error"); }
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
        var evidence = Array.isArray(row.evidence) ? row.evidence.join(" · ") : String(row.evidence || "—");
        var actions = row.confirmed
            ? '<button class="btn btn-secondary btn-sm" data-term-edit="' + escapeHtml(row.source) + '">编辑</button><button class="btn btn-ghost btn-sm" data-term-delete="' + escapeHtml(row.source) + '">移除</button>'
            : '<button class="btn btn-primary btn-sm" data-term-promote="' + escapeHtml(row.source) + '">确认译法</button><button class="btn btn-ghost btn-sm" data-term-delete="' + escapeHtml(row.source) + '">拒绝</button>';
        return "<tr><td><b>" + escapeHtml(row.source) + "</b></td><td>" + escapeHtml(row.target || "尚无稳定候选") + '</td><td><span class="glossary-status ' + escapeHtml(row.status) + '">' + escapeHtml(row.status) + "</span></td><td>" + escapeHtml(evidence || "—") + '</td><td><div class="term-actions">' + actions + "</div></td></tr>";
    }).join("");
}

async function editGlossaryTerm(source) {
    var current = (state.glossary.payload.terms || {})[source] || candidateTarget((state.glossary.payload.candidates || {})[source]);
    var target = window.prompt("编辑确认译法：\n" + source, current || "");
    if (target == null || !target.trim()) return;
    try {
        var result = await API.put("/glossary/dynamic/" + encodeURIComponent(source), {
            file_path: activeFilePath(), source: source, target: target.trim(),
        });
        toast("术语已更新；同步影响 " + Number(result.updated_cells || 0) + " 个译文", "success");
        await loadGlossary();
        await refreshReviewCount();
    } catch (error) { toast(error.message, "error"); }
}

async function promoteGlossaryTerm(source) {
    var suggested = candidateTarget((state.glossary.payload.candidates || {})[source]);
    var target = window.prompt("确认术语译法：\n" + source, suggested || "");
    if (target == null || !target.trim()) return;
    try {
        var result = await API.post("/glossary/promote", {
            file_path: activeFilePath(), source: source, target: target.trim(),
        });
        toast("术语已确认；同步影响 " + Number(result.updated_cells || 0) + " 个译文", "success");
        await loadGlossary();
        await refreshReviewCount();
    } catch (error) { toast(error.message, "error"); }
}

async function deleteGlossaryTerm(source) {
    if (!window.confirm("移除术语“" + source + "”？已生成译文不会被自动回滚。")) return;
    try {
        await API.delete("/glossary/dynamic/" + encodeURIComponent(source) + "?file_path=" + encodeURIComponent(activeFilePath()));
        toast("术语已移除", "success");
        await loadGlossary();
    } catch (error) { toast(error.message, "error"); }
}
