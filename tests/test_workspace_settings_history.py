from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


DOM_HARNESS = r'''
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const html = fs.readFileSync("ui/templates/index.html", "utf8"), nodes = {};
for (const [, tag, id] of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"/g)) {
    nodes[id] = {
        tag, _value: "", _html: "", checked: false, disabled: false, hidden: false, listeners: {},
        classList: {toggle() {}}, setAttribute() {}, querySelector() { return {textContent: ""}; },
        addEventListener(kind, callback) { this.listeners[kind] = callback; },
        get value() { return this._value; },
        set value(value) { this._value = this.tag === "select" && this._html && !this._html.includes('value="' + value + '"') ? "" : value; },
        get innerHTML() { return this._html; },
        set innerHTML(value) { this._html = value; this._value = (value.match(/value="([^"]*)"/) || [0, ""])[1]; },
    };
}
const context = {
    el: id => { assert.ok(nodes[id], "Missing element " + id); return nodes[id]; }, all: () => [],
    escapeHtml: value => String(value || ""), modelLabel: value => String(value || "").replace(/^api:/, ""),
    toast() {}, formatDate: String, setTimeout, clearTimeout,
};
vm.createContext(context);
'''


def run_node(script: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for UI workflow checks")
    result = subprocess.run(
        [node, "-"], cwd=Path(__file__).resolve().parents[1],
        input=DOM_HARNESS + script, text=True, capture_output=True, encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_first_connection_reads_models_then_finishes_without_manual_intermediate_save():
    run_node(r'''
const settings = {
    provider: "api", active_api_profile: "design", default_model: "api:",
    api: {preset: "opendesign_go", style: "openai", base_url: "https://example.invalid/v1", models: [], api_key_configured: false},
    api_profiles: [{id: "design", name: "Design", preset: "opendesign_go"}, {id: "other", name: "Other", preset: "openai"}],
    provider_presets: [{id: "opendesign_go", name: "Design", style: "openai", base_url: "https://example.invalid/v1"}],
    ollama: {host: "http://localhost:11434"}, file: {writable: true},
};
const calls = [], pages = [];
let benchmarkLoads = 0;
context.state = {settings, settingsDirty: false, settingsConnectionDirty: false, taskStatus: "idle", models: [],
    modelCatalog: {api: [], ollama: []}, modelAvailability: {}, modelNsfwAvailability: {}};
Object.assign(context, {
    modelTestRecord: () => ({status: "untested"}), modelTestTimeSummary: () => "",
    benchmarkIsActive: () => false, renderModelBenchmark() {}, bindModelBenchmarkEvents() {},
    loadPersistedModelStatuses() {}, loadModels: async () => {}, invalidatePreflight() {},
    loadModelBenchmark: async () => { assert.equal(context.state.modelBenchmark, null); benchmarkLoads++; },
    navigate: page => pages.push(page), API: {
        put: async (path, body) => {
            calls.push({path, body});
            Object.assign(settings, {default_model: body.default_model || "api:"});
            settings.api = {...settings.api, models: body.api_models, api_key_configured: true};
            return {settings};
        },
        post: async (path, body) => {
            calls.push({path, body});
            if (path.endsWith("discover")) return {ok: true, models: [{id: "api:first", name: "first"}, {id: "api:second", name: "second"}]};
            return {settings: {...settings, active_api_profile: body.profile_id}};
        },
    },
});
vm.runInContext(fs.readFileSync("ui/static/js/settings.js", "utf8"), context);
(async () => {
    context.initializeModelCatalog(); context.renderSettings(); context.bindSettingsEvents();
    nodes["settings-api-key"].value = "test-only-secret";
    nodes["settings-api-key"].listeners.input.call(nodes["settings-api-key"]);
    assert.equal(nodes["btn-settings-connect"].disabled, false);
    await context.connectSettingsProvider();
    assert.equal(calls.length, 2);
    assert.equal(calls[0].path, "/settings");
    assert.equal(calls[0].body.api_key_action, "replace");
    assert.equal(calls[0].body.default_model, "");
    assert.equal(calls[1].path, "/settings/models/discover");
    assert.equal(context.state.modelCatalog.api.length, 2);
    assert.equal(nodes["settings-default-model"].value, "");
    assert.equal(nodes["btn-settings-save"].disabled, true);
    nodes["settings-default-model"].value = "api:second";
    nodes["settings-default-model"].listeners.change();
    assert.equal(nodes["btn-settings-save"].disabled, false);
    await context.saveSettings({preventDefault() {}});
    assert.equal(calls.length, 3);
    assert.equal(calls[2].body.default_model, "api:second");
    assert.equal(calls[2].body.api_models.length, 2);
    assert.equal(calls[2].body.api_key_action, "keep");
    assert.equal(pages.at(-1), "translate");
    assert.equal(benchmarkLoads, 2);
    nodes["settings-api-profile"].value = "other";
    await nodes["settings-api-profile"].listeners.change();
    assert.equal(calls.at(-1).path, "/settings/providers/activate");
    assert.equal(context.state.settings.active_api_profile, "other");
    const count = calls.length;
    context.state.settingsDirty = true;
    nodes["settings-api-profile"].value = "design";
    await nodes["settings-api-profile"].listeners.change();
    assert.equal(calls.length, count, "Unsaved edits must block automatic profile switching");
    context.state.settingsDirty = false; context.state.taskStatus = "running";
    await context.activateApiProvider();
    assert.equal(calls.length, count, "Active translation must block switching");
    assert.match(html, /<details id="settings-model-tools"/);
    assert.match(html, /<details id="model-benchmark-panel"/);
    assert.ok(nodes["btn-test-enabled-models"].listeners.click);
    assert.ok(nodes["btn-test-enabled-nsfw"].listeners.click);
})().catch(error => { console.error(error); process.exitCode = 1; });
''')


def test_history_deduplicates_filters_and_preserves_previous_work_on_open_failure():
    run_node(r'''
const calls = [], pages = [];
let canReplace = true, missing = false, resets = 0;
context.state = {filePath: "E:/current/work.json", sourceFilePath: "E:/current/work.json", review: {}, taskStatus: "idle",
    history: {loading: false, clearingAll: false, deletingPaths: new Set(), candidatePaths: new Set()}};
Object.assign(context, {
    activeFilePath: () => context.state.sourceFilePath,
    ensureCanReplaceFile: async () => canReplace,
    settingsTaskIsActive: () => context.state.taskStatus === "running",
    resetFile: () => { resets++; context.state.filePath = ""; context.state.sourceFilePath = ""; },
    renderFile() {}, refreshExportState: async () => {}, refreshReviewCount: async () => {}, startPolling() {},
    navigate: page => pages.push(page), API: {
        get: async path => { calls.push(path); if (missing) throw new Error("File missing"); return {total_rows: 100, rows: []}; },
        post: async path => { calls.push(path); return {task_id: "resumed"}; },
    },
});
vm.runInContext(fs.readFileSync("ui/static/js/history.js", "utf8"), context);
(async () => {
    const first = {file_path: "E:\\project\\work.json", project_display_name: "Alpha", file_name: "Game.json", completed: 25, total: 100, status: "stopped", file_exists: true};
    const second = {file_path: "E:/done/work.json", project_display_name: "Beta", file_name: "Story.json", completed: 100, total: 100, status: "completed", review_queue_size: 2, file_exists: true};
    const merged = context.mergeHistoryProjects([first], [first, second], [{file_path: "e:/project/work.json", status: "stopped"}]);
    assert.equal(merged.length, 2, "Recovery and history must share one canonical project row");
    assert.equal(merged.find(item => item.project_display_name === "Alpha").recoverable, true);
    context.state.history.projects = merged;
    nodes["history-status-filter"].value = "all";
    context.renderHistoryProjects();
    assert.equal((nodes["history-list"].innerHTML.match(/<article/g) || []).length, 2);
    assert.match(nodes["history-list"].innerHTML, /data-resume-path/);
    assert.match(nodes["history-list"].innerHTML, /<details class="history-more">/);
    nodes["history-search"].value = "Story"; context.renderHistoryProjects();
    assert.ok(nodes["history-list"].innerHTML.includes("Beta"));
    assert.ok(!nodes["history-list"].innerHTML.includes("Alpha"));
    nodes["history-search"].value = ""; nodes["history-status-filter"].value = "unfinished";
    context.renderHistoryProjects();
    assert.ok(nodes["history-list"].innerHTML.includes("Alpha"));
    assert.ok(!nodes["history-list"].innerHTML.includes("Beta"));
    canReplace = false;
    assert.equal(await context.adoptHistoryFile(second.file_path), false);
    assert.equal(calls.length, 0);
    canReplace = true; missing = true;
    assert.equal(await context.adoptHistoryFile(second.file_path), false);
    assert.equal(resets, 0, "A missing project must preserve the current workspace");
    assert.equal(context.state.filePath, "E:/current/work.json");
    await context.resumeHistory(second.file_path);
    assert.ok(!calls.includes("/recovery/resume"), "Failed adoption must never start recovery");
    missing = false;
    assert.equal(await context.adoptHistoryFile(second.file_path), true);
    assert.equal(context.state.filePath, second.file_path);
    assert.equal(context.state.totalRows, 100);
    assert.equal(pages.at(-1), "review");
    assert.ok(calls.every(path => !path.includes("cleanup") && !path.includes("clear")));
    assert.ok(!html.includes('id="btn-clear-history"'));
    assert.ok(!html.includes('id="recovery-list"'));
})().catch(error => { console.error(error); process.exitCode = 1; });
''')
