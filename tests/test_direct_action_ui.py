from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


HARNESS = r'''
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const html = fs.readFileSync("ui/templates/index.html", "utf8"), nodes = {}, storage = new Map(), events = {};
for (const [, id] of html.matchAll(/\bid="([^"]+)"/g)) {
    nodes[id] = {value: "", textContent: "", innerHTML: "", hidden: false, disabled: false, events: {},
        setAttribute() {}, querySelector() {return {textContent: ""};},
        classList: {toggle() {}, add() {}, remove() {}},
        addEventListener(name, listener) {this.events[name] = listener;}};
}
let file = "fixture.json", active = false, resets = 0, failDelete = false, allowFlush = true, deletes = [], posts = [], errors = [];
let glossary = {terms: {}, candidates: {
    "アリス": {status: "candidate", targets: {"爱丽丝": 3}},
    "ボブ": {status: "candidate", targets: {"鲍勃": 2}},
}};
const context = {
    state: {fileName: "fixture.json", glossary: {payload: glossary, search: "", filter: "all"},
        history: {loading: false, clearingAll: false, deletingPaths: new Set(), projects: []},
        review: {aiTaskId: "review-1", aiTaskStatus: "completed", actionBusy: ""}},
    el: id => {assert.ok(nodes[id], id); return nodes[id];}, all: () => [],
    activeFilePath: () => file, settingsTaskIsActive: () => active,
    escapeHtml: value => String(value || "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;"),
    toast: (message, kind) => {if (kind === "error") errors.push(message);},
    localStorage: {getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value)},
    window: {confirm() {throw new Error("A button action must not ask for a second confirmation");},
        addEventListener: (name, listener) => {events[name] = listener;}, openReviewSearch() {}},
    resetFile: () => {resets++; file = "";}, refreshReviewCount: async () => {}, refreshExportState: async () => {},
    flushReviewDraft: async () => allowFlush, reviewActionIsBusy: () => Boolean(context.state.review.actionBusy),
    setReviewActionBusy: kind => {context.state.review.actionBusy = kind || "";},
    clearReviewSelection() {}, refreshReviewAfterEdit: async () => {},
    API: {
        get: async url => {
            if (url.startsWith("/glossary/dynamic")) return glossary;
            if (url.startsWith("/review/ai/current")) return {task: null};
            const p = new URL(url, "https://local.invalid").searchParams;
            return {target: p.get("target"), target_valid: true, entry_count: 0, occurrence_count: 0,
                affected_count: 0, offset: 0, limit: 10, items: []};
        },
        delete: async url => {deletes.push(url); if (failDelete) throw new Error("delete failed"); return {ok: true};},
        post: async (url, payload) => {posts.push({url, payload}); return {restored: 1, skipped: 0, updated_cells: 1};},
    },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync("ui/static/js/glossary.js", "utf8"), context);
vm.runInContext(fs.readFileSync("ui/static/js/history.js", "utf8"), context);
vm.runInContext(fs.readFileSync("ui/static/js/ai-review.js", "utf8"), context);
context.loadHistory = async () => {};
context.loadRecoveryBanner = async () => {};
context.loadCurrentAIReview = async () => {};
'''


def run_ui(script: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for direct button behavior checks")
    result = subprocess.run(
        [node, "-"], cwd=Path(__file__).resolve().parents[1],
        input=HARNESS + "\n(async () => {\n" + script + "\n})().catch(error => {console.error(error); process.exitCode = 1;});\n",
        text=True, capture_output=True, encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_delete_buttons_execute_once_without_confirmation_and_keep_failure_guards():
    run_ui(r'''
context.initGlossaryWorkspace();
await context.deleteGlossaryTerm("アリス");
assert.equal(deletes[0], "/glossary/dynamic/" + encodeURIComponent("アリス") + "?file_path=fixture.json");
context.state.glossary.detailBusy = true;
await context.deleteGlossaryTerm("ボブ");
assert.equal(deletes.length, 1);
context.state.glossary.detailBusy = false;
failDelete = true;
await context.deleteHistory(file);
assert.equal(deletes.length, 2);
assert.equal(resets, 0, "A failed history deletion must preserve the current workspace");
assert.equal(context.state.history.deletingPaths.size, 0);
assert.ok(errors.includes("delete failed"));
failDelete = false;
active = true;
await context.deleteHistory(file);
assert.equal(deletes.length, 2, "The running project must remain protected");
active = false;
await context.deleteHistory(file);
assert.equal(deletes.length, 3);
assert.equal(resets, 1);
''')


def test_ai_rollback_executes_directly_and_blocks_active_or_unsaved_work():
    run_ui(r'''
await context.rollbackAIReview();
assert.equal(posts.length, 1);
assert.equal(posts[0].url, "/review/ai/review-1/rollback");
assert.equal(posts[0].payload.file_path, "fixture.json");
context.state.review.aiTaskStatus = "reviewing";
await context.rollbackAIReview();
assert.equal(posts.length, 1);
context.state.review.aiTaskStatus = "completed";
allowFlush = false;
await context.rollbackAIReview();
assert.equal(posts.length, 1, "Unwritten editor drafts must still block rollback");
''')


def test_glossary_leaving_keeps_unapplied_drafts_without_promoting_terms():
    run_ui(r'''
context.initGlossaryWorkspace();
await context.openGlossaryDetail("アリス");
nodes["glossary-detail-target"].value = "未应用的新译法";
nodes["glossary-detail-target"].events.input.call(nodes["glossary-detail-target"]);
assert.equal(context.confirmGlossaryLeave(), true);
context.closeGlossaryDetail(false);
assert.equal(nodes["glossary-detail"].hidden, true);
await context.openGlossaryDetail("ボブ");
await context.openGlossaryDetail("アリス");
assert.equal(nodes["glossary-detail-target"].value, "未应用的新译法");
assert.equal(context.state.glossary.detailDirty, true);
assert.ok(storage.get("lgt.glossaryDrafts").includes("未应用的新译法"));
assert.equal(posts.length, 0, "Saving a local draft must never confirm terminology");
assert.deepEqual(glossary.terms, {});
failDelete = true;
await context.deleteGlossaryTerm("アリス");
assert.ok(storage.get("lgt.glossaryDrafts").includes("未应用的新译法"), "Failed deletion must retain the pending glossary draft");
''')


def test_late_native_drafts_do_not_overwrite_new_edits_or_revive_applied_terms():
    run_ui(r'''
let resolveNative;
const nativeWrites = [];
context.window.pywebview = {api: {
    get_workspace_drafts: kind => {assert.equal(kind, "glossary"); return new Promise(resolve => {resolveNative = resolve;});},
    set_workspace_drafts: async (kind, drafts) => {nativeWrites.push({kind, drafts: structuredClone(drafts)}); return {ok: true};},
}};
context.initGlossaryWorkspace();
await context.openGlossaryDetail("アリス");
nodes["glossary-detail-target"].value = "当前输入";
nodes["glossary-detail-target"].events.input.call(nodes["glossary-detail-target"]);
await context.previewGlossaryImpact(0);
await context.applyGlossaryDetail({preventDefault() {}});
assert.equal(posts[0].payload.target, "当前输入");
resolveNative([{file, source: "アリス", target: "较旧草稿", initialTarget: "爱丽丝"},
    {file, source: "ボブ", target: "待处理的鲍勃", unexpected: "must not be forwarded"}]);
await new Promise(resolve => setImmediate(resolve));
assert.equal(nodes["glossary-detail-target"].value, "当前输入");
const saved = nativeWrites.at(-1);
assert.equal(saved.kind, "glossary");
assert.equal(saved.drafts.length, 1, "An explicitly applied term must not be revived by a late native read");
assert.equal(saved.drafts[0].source, "ボブ");
assert.ok(!("unexpected" in saved.drafts[0]));
await context.openGlossaryDetail("ボブ");
assert.equal(nodes["glossary-detail-target"].value, "待处理的鲍勃");
''')


def test_restored_glossary_draft_keeps_its_baseline_and_shows_a_stale_hint():
    run_ui(r'''
storage.set("lgt.glossaryDrafts", JSON.stringify([{file, source: "アリス", target: "旧的未应用草稿", initialTarget: "爱丽丝"}]));
glossary.terms["アリス"] = "当前已确认译法";
context.initGlossaryWorkspace();
await context.openGlossaryDetail("アリス");
assert.equal(nodes["glossary-detail-target"].value, "旧的未应用草稿");
assert.match(nodes["glossary-detail-note"].textContent, /已有更新/);
assert.equal(posts.length, 0);
nodes["glossary-detail-target"].value = "继续编辑的草稿";
nodes["glossary-detail-target"].events.input.call(nodes["glossary-detail-target"]);
assert.equal(JSON.parse(storage.get("lgt.glossaryDrafts"))[0].initialTarget, "爱丽丝");
assert.equal(glossary.terms["アリス"], "当前已确认译法");
''')
