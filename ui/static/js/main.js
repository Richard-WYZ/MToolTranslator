"use strict";

function bindEvents() {
    window.addEventListener("hashchange", function () { showPage(location.hash.slice(1)); });
    all("[data-nav]").forEach(function (node) { node.addEventListener("click", function () { navigate(node.dataset.nav); }); });
    el("btn-refresh-all").addEventListener("click", function () {
        if (state.page === "translate") loadModels(true);
        else if (state.page === "review") loadReview();
        else if (state.page === "glossary") loadGlossary();
        else if (state.page === "history") loadHistory();
        else if (state.page === "settings") loadSettings(true);
        else loadModels(true);
    });
    el("btn-refresh-models").addEventListener("click", function () { loadModels(true); });
    bindSettingsEvents();
    el("btn-refresh-history").addEventListener("click", loadHistory);

    var zone = el("upload-zone");
    zone.addEventListener("click", function () { if (!zone.classList.contains("disabled")) el("file-input").click(); });
    zone.addEventListener("keydown", function (event) { if ((event.key === "Enter" || event.key === " ") && !zone.classList.contains("disabled")) el("file-input").click(); });
    zone.addEventListener("dragover", function (event) { event.preventDefault(); zone.classList.add("dragover"); });
    zone.addEventListener("dragleave", function () { zone.classList.remove("dragover"); });
    zone.addEventListener("drop", function (event) {
        event.preventDefault();
        zone.classList.remove("dragover");
        if (!zone.classList.contains("disabled")) handleFile(event.dataTransfer.files[0]);
    });
    el("file-input").addEventListener("change", function () { handleFile(this.files[0]); });
    el("btn-remove-file").addEventListener("click", async function () { if (await ensureCanReplaceFile()) resetFile(); });
    el("btn-preview-toggle").addEventListener("click", function () {
        el("preview-wrap").hidden = !el("preview-wrap").hidden;
        this.textContent = el("preview-wrap").hidden ? "预览" : "收起";
    });

    all('input[name="execution-profile"]').forEach(function (input) {
        input.addEventListener("change", function () {
            state.profile = input.value;
            renderProfile();
        });
    });
    ["model-select", "fast-model", "sensitive-model", "quality-model", "custom-concurrency", "custom-batch-size", "custom-max-chars", "custom-protocol"].forEach(function (id) {
        el(id).addEventListener("change", function () {
            if (id === "model-select" && this.value) localStorage.setItem("lgt.selectedModel", this.value);
            invalidatePreflight();
        });
    });

    el("btn-preflight").addEventListener("click", runPreflight);
    el("btn-start").addEventListener("click", startTranslation);
    el("btn-pause").addEventListener("click", pauseTranslation);
    el("btn-resume").addEventListener("click", resumeTranslation);
    el("btn-stop").addEventListener("click", stopTranslation);
    el("btn-export").addEventListener("click", exportResult);

    el("review-filters").addEventListener("click", function (event) {
        var button = event.target.closest("[data-filter]");
        if (!button) return;
        state.review.filter = button.dataset.filter;
        state.review.offset = 0;
        state.review.selectedRows.clear();
        all("#review-filters button").forEach(function (node) { node.classList.toggle("active", node === button); });
        loadReviewList(0).catch(function (error) { toast(error.message, "error"); });
    });
    el("review-queue").addEventListener("click", function (event) {
        var checkbox = event.target.closest("[data-review-check]");
        if (checkbox) {
            var row = Number(checkbox.dataset.reviewCheck);
            if (checkbox.checked) state.review.selectedRows.add(row); else state.review.selectedRows.delete(row);
            el("btn-accept-selected").disabled = state.review.selectedRows.size === 0;
            event.stopPropagation();
            return;
        }
        var item = event.target.closest("[data-review-row]");
        if (item) selectReviewRow(Number(item.dataset.reviewRow));
    });
    el("btn-review-prev").addEventListener("click", function () { loadReviewList(state.review.offset - state.review.limit).catch(function (error) { toast(error.message, "error"); }); });
    el("btn-review-next").addEventListener("click", function () { loadReviewList(state.review.offset + state.review.limit).catch(function (error) { toast(error.message, "error"); }); });
    el("btn-review-jump").addEventListener("click", jumpReview);
    el("btn-review-accept").addEventListener("click", function () { saveReview("accept"); });
    el("btn-review-draft").addEventListener("click", function () { saveReview("draft"); });
    el("btn-review-preserve").addEventListener("click", function () { saveReview("preserve"); });
    el("btn-accept-selected").addEventListener("click", acceptSelected);

    el("glossary-search").addEventListener("input", function () { state.glossary.search = this.value; renderGlossary(); });
    el("glossary-tabs").addEventListener("click", function (event) {
        var button = event.target.closest("[data-glossary-filter]");
        if (!button) return;
        state.glossary.filter = button.dataset.glossaryFilter;
        all("#glossary-tabs button").forEach(function (node) { node.classList.toggle("active", node === button); });
        renderGlossary();
    });
    el("glossary-body").addEventListener("click", function (event) {
        var edit = event.target.closest("[data-term-edit]");
        var promote = event.target.closest("[data-term-promote]");
        var remove = event.target.closest("[data-term-delete]");
        if (edit) editGlossaryTerm(edit.dataset.termEdit);
        else if (promote) promoteGlossaryTerm(promote.dataset.termPromote);
        else if (remove) deleteGlossaryTerm(remove.dataset.termDelete);
    });

    el("recovery-list").addEventListener("click", function (event) {
        var button = event.target.closest("[data-resume-path]");
        if (button) resumeHistory(button.dataset.resumePath);
    });
    el("history-list").addEventListener("click", function (event) {
        var button = event.target.closest("[data-open-path]");
        if (button) adoptHistoryFile(button.dataset.openPath);
    });
    window.addEventListener("beforeunload", function (event) {
        if (state.settingsDirty || state.hasUnexportedResult || ["running", "paused", "stopping"].includes(state.taskStatus)) {
            event.preventDefault();
            event.returnValue = "";
        }
    });
}

async function init() {
    bindEvents();
    renderFile();
    resetTaskDisplay();
    await Promise.all([checkServer(), loadModels(false), loadRecoveryBanner()]);
    showPage(location.hash.slice(1) || "translate");
    setInterval(checkServer, 30000);
}

window.LGT = {
    API: API,
    state: state,
    navigate: navigate,
    loadModels: loadModels,
    loadReview: loadReview,
    loadGlossary: loadGlossary,
};

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
else init();
