from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const html = fs.readFileSync("ui/templates/index.html", "utf8");
const nodes = {};
for (const [, id] of html.matchAll(/\bid="([^"]+)"/g)) {
    nodes[id] = { value: "", innerHTML: "", textContent: "", hidden: false, disabled: false,
        checked: false, readOnly: false, dataset: {}, style: {},
        classList: {add() {}, remove() {}, toggle() {}}, setAttribute() {}, hasAttribute() { return false; },
        addEventListener() {}, closest() { return null; } };
}
const storage = new Map(), posts = [], gets = [], windowListeners = {};
let filePath = "fixture.json", failWrites = false, customPost = null, customGet = null;
const backendItems = [
    {row: 0, columns: [{original: "原文甲", translated: "译文甲", status: "translated", manual_confirmed: false}], neighbors: []},
    {row: 1, columns: [{original: "原文乙", translated: "译文乙", status: "translated_needs_review", manual_confirmed: false}], neighbors: []},
];
const context = {
    state: {page: "review", hasUnexportedResult: false, exportReady: true, models: [], review: {
        filter: "all", query: "", searchScope: "both", offset: 0, limit: 30, total: 2, items: [],
        selectedRow: null, selectedRows: new Set(), selectedItems: new Map(), listRequestId: 0,
        loadRequestId: 0, aiTaskStatus: "idle", aiPollingGeneration: 0,
    }},
    el: id => { assert.ok(nodes[id], "Missing DOM node: " + id); return nodes[id]; }, all: () => [],
    activeFilePath: () => filePath,
    escapeHtml: value => String(value == null ? "" : value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;"),
    modelLabel: value => value || "", formatDate: () => "", formatDuration: () => "", toast: () => {},
    localStorage: {getItem: () => null, setItem() {}},
    sessionStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value) },
    setTimeout, clearTimeout, AbortController,
    navigate: page => {context.state.page = page;},
    document: {addEventListener() {}}, window: {addEventListener: (name, handler) => {windowListeners[name] = handler;}, confirm: () => true},
    API: {
        get: async url => {
            gets.push(url);
            if (customGet) return customGet(url);
            if (url.startsWith("/review/stats")) return {total: 2};
            if (url.startsWith("/review/list")) {
                const params = new URL(url, "https://local.example").searchParams;
                return {items: structuredClone(backendItems), matched_total: 2, offset: Number(params.get("offset"))};
            }
            return {};
        },
        post: async (url, body) => {
            posts.push({url, body});
            if (customPost) return customPost(url, body);
            if (failWrites) throw new Error("disk unavailable");
            const column = backendItems[body.row].columns[0];
            column.translated = body.text;
            column.status = body.action === "accept" ? "translated" : "translated_needs_review";
            column.manual_confirmed = body.action === "accept";
            return {text: body.text, violations: [], status: body.action};
        },
    },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync("ui/static/js/review.js", "utf8"), context);
vm.runInContext(fs.readFileSync("ui/static/js/ai-review.js", "utf8"), context);
function seed() {
    context.state.review.items = structuredClone(backendItems);
    context.state.review.selectedRow = 0;
    context.renderReviewEditor(context.state.review.items[0]);
}
'''


def run_ui(script: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the review workspace behavior checks")
    result = subprocess.run(
        [node, "-"],
        cwd=ROOT,
        input=HARNESS + "\n(async () => {\n" + script + "\n})().catch(error => { console.error(error); process.exitCode = 1; });\n",
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_draft_autosave_never_confirms_and_failure_keeps_the_editor():
    run_ui(r'''
seed();
nodes["editor-target"].value = "修改后的草稿";
context.captureReviewDraft();
assert.ok(storage.get("lgt.reviewDrafts").includes("修改后的草稿"));
failWrites = true;
await context.selectReviewRow(1);
assert.equal(context.state.review.selectedRow, 0, "A failed save must not navigate away");
assert.equal(nodes["editor-target"].value, "修改后的草稿");
assert.match(nodes["editor-save-note"].textContent, /保存失败/);
assert.equal(posts[0].body.action, "draft");
assert.equal(backendItems[0].columns[0].manual_confirmed, false);
failWrites = false;
await context.selectReviewRow(1);
assert.equal(context.state.review.selectedRow, 1);
assert.equal(backendItems[0].columns[0].translated, "修改后的草稿");
assert.equal(backendItems[0].columns[0].status, "translated_needs_review");
assert.equal(storage.get("lgt.reviewDrafts"), "[]");
assert.equal(backendItems[0].columns[0].original, "原文甲");
''')


def test_edits_made_during_autosave_are_drained_in_order():
    run_ui(r'''
seed();
let release;
customPost = async (url, body) => {
    if (posts.length === 1) await new Promise(resolve => { release = resolve; });
    return {text: body.text};
};
nodes["editor-target"].value = "第一版";
const saving = context.flushReviewDraft();
await new Promise(resolve => setImmediate(resolve));
nodes["editor-target"].value = "第二版";
context.captureReviewDraft();
release();
assert.equal(await saving, true);
assert.deepEqual(posts.map(post => post.body.text), ["第一版", "第二版"]);
assert.ok(posts.every(post => post.body.action === "draft"));
assert.equal(context.state.review.editorSavedText, "第二版");
assert.equal(nodes["editor-target"].value, "第二版");
assert.equal(storage.get("lgt.reviewDrafts"), "[]");
''')


def test_search_and_jump_share_full_file_query_and_highlights_escape_markup():
    run_ui(r'''
seed();
context.state.review.query = "<tag>";
context.state.review.searchScope = "source";
assert.equal(context.reviewHighlighted("前<tag>后", "source"), "前<mark>&lt;tag&gt;</mark>后");
assert.equal(context.reviewHighlighted("前<tag>后", "target"), "前&lt;tag&gt;后");
context.state.review.query = "译文";
context.state.review.searchScope = "target";
customGet = async url => {
    if (url.startsWith("/review/jump")) return {found: true, offset: 30};
    return {items: structuredClone(backendItems), matched_total: 62, offset: url.includes("offset=30") ? 30 : 0};
};
await context.loadReviewList(0);
assert.equal(context.state.review.total, 62);
assert.match(nodes["review-search-count"].textContent, /62/);
assert.ok(gets[0].includes("query=" + encodeURIComponent("译文")));
assert.ok(gets[0].includes("scope=target"));
await context.jumpToReviewRow(1);
assert.ok(gets.find(url => url.startsWith("/review/jump")).includes("scope=target"));
assert.equal(context.state.review.offset, 30);
assert.match(nodes["editor-target-matches"].innerHTML, /<mark>译文<\/mark>/);
''')


def test_stale_search_results_cannot_replace_the_current_query():
    run_ui(r'''
seed();
let release;
customGet = async url => {
    if (url.includes("query=old")) return new Promise(resolve => {release = resolve;});
    return {items: [structuredClone(backendItems[1])], matched_total: 1, offset: 0};
};
context.state.review.query = "old";
const oldRequest = context.loadReviewList(0);
await new Promise(resolve => setImmediate(resolve));
context.state.review.query = "new";
await context.loadReviewList(0);
release({items: [structuredClone(backendItems[0])], matched_total: 99, offset: 0});
await oldRequest;
assert.equal(context.state.review.total, 1);
assert.equal(context.state.review.items[0].row, 1);
''')


def test_manual_confirmation_advances_and_ai_search_range_is_separate():
    run_ui(r'''
seed();
await context.saveReview("accept");
assert.equal(posts[0].body.action, "accept");
assert.equal(backendItems[0].columns[0].manual_confirmed, true);
assert.equal(context.state.review.selectedRow, 1);
await context.selectReviewRow(0);
assert.equal(nodes["editor-status"].textContent, "人工已确认");
context.state.review.query = "原文";
context.state.review.searchScope = "source";
const request = context.aiReviewRequest("filter", []);
assert.equal(request.scope, "filter");
assert.equal(request.search_scope, "source");
assert.equal(request.query, "原文");
nodes["editor-target"].value = "";
assert.equal(await context.flushReviewDraft(), false, "An empty editor must remain a local draft");
assert.equal(posts.length, 1);
''')


def test_native_pending_draft_cache_only_receives_boolean_changes():
    run_ui(r'''
const pendingStates = [];
context.window.pywebview = {api: {set_pending_review_draft: pending => {
    assert.equal(typeof pending, "boolean");
    pendingStates.push(pending);
    return Promise.resolve(pending);
}}};
context.initReviewWorkspace();
assert.deepEqual(pendingStates, [false], "A clean initial workspace must initialize the native cache");
await new Promise(resolve => setImmediate(resolve));
seed();
nodes["editor-target"].value = "本地草稿";
context.captureReviewDraft();
assert.deepEqual(pendingStates, [false, true], "Capture must immediately mark the native cache pending");
context.captureReviewDraft();
context.persistReviewDrafts();
assert.deepEqual(pendingStates, [false, true], "Repeated edits with the same pending state must not push again");
failWrites = true;
assert.equal(await context.flushReviewDraft(), false);
assert.deepEqual(pendingStates, [false, true], "A failed disk write must not clear native pending state");
assert.ok(storage.get("lgt.reviewDrafts").includes("本地草稿"));
failWrites = false;
assert.equal(await context.flushReviewDraft(), true);
assert.deepEqual(pendingStates, [false, true, false]);
assert.equal(storage.get("lgt.reviewDrafts"), "[]");
''')


def test_native_bridge_ready_repushes_restored_drafts_without_discarding_them():
    run_ui(r'''
storage.set("lgt.reviewDrafts", JSON.stringify([{path: filePath, row: 0, text: "恢复草稿", original: "原文甲"}]));
context.initReviewWorkspace();
const pendingStates = [];
context.window.pywebview = {api: {set_pending_review_draft: pending => {
    pendingStates.push(pending);
    return Promise.reject(new Error("bridge disconnected"));
}}};
windowListeners.pywebviewready();
await new Promise(resolve => setImmediate(resolve));
assert.deepEqual(pendingStates, [true]);
assert.ok(storage.get("lgt.reviewDrafts").includes("恢复草稿"));
seed();
assert.equal(nodes["editor-target"].value, "恢复草稿", "A failed bridge push must retain the restored editor text");
context.window.pywebview.api.set_pending_review_draft = pending => {pendingStates.push(pending); return Promise.resolve(pending);};
windowListeners.pywebviewready();
await new Promise(resolve => setImmediate(resolve));
assert.deepEqual(pendingStates, [true, true], "Ready must retry the current state after bridge reconnection");
assert.equal(await context.flushReviewDraft(), true);
assert.equal(posts.length, 0, "A legacy restored draft must wait for explicit save or actual input");
await context.saveReview("draft");
assert.deepEqual(pendingStates, [true, true, false]);
''')


def test_native_pending_pushes_are_serialized_when_disk_save_finishes_first():
    run_ui(r'''
const pendingStates = [];
let finishPendingPush;
context.window.pywebview = {api: {set_pending_review_draft: pending => {
    pendingStates.push(pending);
    return pending ? new Promise(resolve => {finishPendingPush = resolve;}) : Promise.resolve(pending);
}}};
context.initReviewWorkspace();
await new Promise(resolve => setImmediate(resolve));
seed();
nodes["editor-target"].value = "很快保存的草稿";
context.captureReviewDraft();
assert.deepEqual(pendingStates, [false, true]);
assert.equal(await context.flushReviewDraft(), true);
assert.equal(storage.get("lgt.reviewDrafts"), "[]");
assert.deepEqual(pendingStates, [false, true], "The clear push must wait for the earlier pending push");
finishPendingPush(true);
await new Promise(resolve => setImmediate(resolve));
assert.deepEqual(pendingStates, [false, true, false], "The latest saved state must be delivered after the slow pending push");
context.persistReviewDrafts();
assert.deepEqual(pendingStates, [false, true, false]);
''')


def test_pagehide_keeps_recovery_text_without_triggering_a_leave_dialog():
    run_ui(r'''
const durable = new Map();
context.localStorage = {getItem:key=>durable.get(key)||null,setItem:(key,value)=>durable.set(key,value)};
context.initReviewWorkspace();
seed();
nodes["editor-target"].value = "退出前的未保存草稿";
assert.equal(windowListeners.beforeunload, undefined);
windowListeners.pagehide();
assert.ok(durable.get("lgt.reviewDrafts").includes("退出前的未保存草稿"));
assert.equal(posts.length, 0, "Retaining a recovery copy must not accept or apply it");
storage.clear();
context.reviewDrafts.clear();
context.reviewDraftTouched.clear();
context.initReviewWorkspace();
seed();
assert.equal(nodes["editor-target"].value,"退出前的未保存草稿");
''')


def test_native_recovery_is_loaded_before_pushing_and_does_not_auto_apply():
    run_ui(r'''
const nativeWrites = [];
context.window.pywebview = {api:{
    get_workspace_drafts:async kind=>{
        assert.equal(kind,"review");
        return [{path:filePath,row:0,text:"原生恢复草稿",original:"原文甲",base_text:"译文甲"}];
    },
    set_workspace_drafts:async(kind,drafts)=>{nativeWrites.push({kind,drafts});return {ok:true};},
}};
context.initReviewWorkspace();
seed();
await context.restoreReviewDraftStore();
await new Promise(resolve=>setImmediate(resolve));
assert.equal(nodes["editor-target"].value,"原生恢复草稿");
assert.ok(nativeWrites.length);
assert.equal(nativeWrites[0].drafts[0].text,"原生恢复草稿","An initial empty editor must not clear unread native drafts");
assert.equal(posts.length,0);
''')


def test_delayed_recovery_does_not_resurrect_a_saved_draft_or_replace_new_input():
    run_ui(r'''
let restore;
context.window.pywebview={api:{
    get_workspace_drafts:()=>new Promise(resolve=>{restore=resolve;}),
    set_workspace_drafts:async()=>({ok:true}),
}};
context.initReviewWorkspace();
await new Promise(resolve=>setImmediate(resolve));
seed();
nodes["editor-target"].value="刚输入并保存的新草稿";
context.captureReviewDraft();
assert.equal(await context.flushReviewDraft(),true);
assert.equal(posts[0].body.expected_original,"原文甲");
restore([{path:filePath,row:0,text:"迟到的旧草稿",original:"原文甲",base_text:"译文甲"}]);
await context.restoreReviewDraftStore();
assert.equal(context.reviewDrafts.size,0);
assert.equal(nodes["editor-target"].value,"刚输入并保存的新草稿");
''')


def test_old_native_draft_cannot_overwrite_a_newer_save_after_restart():
    run_ui(r'''
context.window.pywebview={api:{
    get_workspace_drafts:async()=>[{path:filePath,row:0,text:"清除失败后残留的旧稿",original:"原文甲",base_text:"更早的译文"}],
    set_workspace_drafts:async()=>{throw new Error("Native clear failed");},
}};
context.initReviewWorkspace();
await context.restoreReviewDraftStore();
seed();
assert.equal(nodes["editor-target"].value,"译文甲","Recovery must not replace a newer saved translation");
assert.equal(await context.flushReviewDraft(),true);
assert.equal(posts.length,0,"A stale native copy must never be auto-applied on blur");
''')


@pytest.mark.parametrize("source", ["session", "local"])
def test_legacy_browser_draft_remains_visible_but_never_overwrites_until_explicit_save(source):
    run_ui(r'''
const durable = new Map();
context.localStorage = {getItem:key=>durable.get(key)||null,setItem:(key,value)=>durable.set(key,value)};
backendItems[0].columns[0].translated = "较新的已保存译文";
const legacy = [{path:filePath,row:0,text:"旧版未写回的人工作品",original:"原文甲"}];
("__SOURCE__" === "session" ? storage : durable).set("lgt.reviewDrafts",JSON.stringify(legacy));
context.initReviewWorkspace();
seed();
assert.equal(nodes["editor-target"].value,"旧版未写回的人工作品");
assert.match(nodes["editor-save-note"].textContent,/旧版草稿尚未写回/);
assert.equal(await context.flushReviewDraft(),true);
assert.equal(posts.length,0,"Reading or blurring a legacy draft must not apply it");
assert.equal(backendItems[0].columns[0].translated,"较新的已保存译文");
assert.equal(context.reviewDrafts.get(context.reviewDraftKey(filePath,0)).base_text,undefined);
assert.ok(storage.get("lgt.reviewDrafts").includes("旧版未写回的人工作品"));
await context.saveReview("draft");
assert.equal(posts.length,1,"One explicit save is sufficient without an extra confirmation");
assert.equal(posts[0].body.action,"draft");
assert.equal(posts[0].body.expected_original,"原文甲");
assert.equal(posts[0].body.expected_translation,"较新的已保存译文");
assert.equal(backendItems[0].columns[0].translated,"旧版未写回的人工作品");
assert.equal(context.reviewDrafts.size,0);
'''.replace("__SOURCE__", source))


@pytest.mark.parametrize("source", ["session", "local"])
def test_actual_input_after_legacy_restore_establishes_a_baseline_for_autosave(source):
    run_ui(r'''
const durable = new Map();
context.localStorage = {getItem:key=>durable.get(key)||null,setItem:(key,value)=>durable.set(key,value)};
backendItems[0].columns[0].translated = "当前已保存译文";
("__SOURCE__" === "session" ? storage : durable).set("lgt.reviewDrafts",JSON.stringify([
    {path:filePath,row:0,text:"旧版草稿",original:"原文甲"},
]));
context.initReviewWorkspace();
seed();
nodes["editor-target"].value = "实际继续编辑后的译文";
context.scheduleReviewDraft();
assert.equal(context.reviewDrafts.get(context.reviewDraftKey(filePath,0)).base_text,"当前已保存译文");
assert.equal(await context.flushReviewDraft(),true);
assert.equal(posts.length,1);
assert.equal(posts[0].body.expected_translation,"当前已保存译文");
assert.equal(posts[0].body.text,"实际继续编辑后的译文");
assert.equal(backendItems[0].columns[0].manual_confirmed,false);
assert.equal(context.reviewDrafts.size,0);
'''.replace("__SOURCE__", source))


def test_native_legacy_recovery_copy_is_retained_for_explicit_save():
    run_ui(r'''
context.window.pywebview={api:{
    get_workspace_drafts:async()=>[{path:filePath,row:0,text:"旧格式本机恢复草稿",original:"原文甲"}],
    set_workspace_drafts:async()=>({ok:true}),
}};
context.initReviewWorkspace();
await context.restoreReviewDraftStore();
seed();
assert.equal(nodes["editor-target"].value,"旧格式本机恢复草稿");
assert.equal(await context.flushReviewDraft(),true);
assert.equal(posts.length,0);
assert.equal(context.reviewDrafts.size,1);
''')
