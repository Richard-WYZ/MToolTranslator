from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


HARNESS = r'''
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const html = fs.readFileSync("ui/templates/index.html", "utf8"), nodes = {};
for (const [, , id] of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"/g)) {
    nodes[id] = {value: "", listeners: {}, disabled: false,
        addEventListener(event, listener) { this.listeners[event] = listener; }};
}
const requests = [], notices = [], busy = [];
let allowSave = true, failModel = "", failBenchmark = false;
const context = {
    state: {settingsDirty: false, settingsBusy: "", taskStatus: "idle", modelBenchmark: {status: "idle"},
        modelCatalog: {api: [{id: "api:model-a", enabled: true}, {id: "api:model-b", enabled: true}, {id: "api:disabled", enabled: false}]},
        modelAvailability: {}, modelNsfwAvailability: {}},
    el: id => { assert.ok(nodes[id], "Missing element " + id); return nodes[id]; },
    window: {confirm() { throw new Error("A button click must not open a confirmation dialog"); }},
    toast: (message, kind) => notices.push({message, kind}),
    modelLabel: value => value, all: () => [],
    flushSettingsAutosave: async () => allowSave,
    API: {post: async (path, payload) => {
        requests.push({path, payload});
        if (path === "/settings/benchmark/start") {
            if (failBenchmark) throw new Error("benchmark request failed");
            return {status: "running"};
        }
        if (payload.model === failModel) throw new Error("model request failed");
        return {nsfw_supported: true, test_status: {status: "available"}};
    }},
};
vm.createContext(context);
for (const file of ["model-testing", "model-benchmark", "settings"]) {
    vm.runInContext(fs.readFileSync("ui/static/js/" + file + ".js", "utf8"), context);
}
context.currentModelCatalog = () => context.state.modelCatalog.api;
context.currentSettingsProvider = () => "api";
context.settingsTaskIsActive = () => ["starting", "running", "paused", "stopping", "finalizing"].includes(context.state.taskStatus);
context.setSettingsBusy = mode => { context.state.settingsBusy = mode; busy.push(mode); };
context.renderSettingsModelPicker = () => {};
context.renderModelBenchmark = () => {};
context.scheduleBenchmarkPoll = () => {};
context.bindSettingsEvents();
async function settle() { for (let index = 0; index < 12; index++) await Promise.resolve(); }
'''


def run_ui(script: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for model action interaction checks")
    result = subprocess.run(
        [node, "-"], cwd=Path(__file__).resolve().parents[1], input=HARNESS + script,
        text=True, capture_output=True, encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("kind,button,store", [
    ("basic", "btn-test-enabled-models", "modelAvailability"),
    ("adult", "btn-test-enabled-nsfw", "modelNsfwAvailability"),
])
def test_batch_model_test_buttons_request_immediately_without_confirmation(kind, button, store):
    run_ui(f'''
(async () => {{
    nodes[{button!r}].listeners.click();
    await settle();
    assert.equal(requests.length, 2);
    assert.ok(requests.every(request => request.path === "/settings/connection-test"));
    assert.equal(requests[0].payload.model, "api:model-a");
    assert.equal(requests[1].payload.model, "api:model-b");
    assert.ok(requests.every(request => request.payload.test_kind === {kind!r} && request.payload.protocol === "auto"));
    assert.equal(context.state[{store!r}]["api:model-a"].status, "available");
    assert.equal(context.state.settingsBusy, "");
}})().catch(error => {{ console.error(error); process.exitCode = 1; }});
''')


def test_benchmark_button_starts_directly_and_keeps_requested_depth_and_enabled_models():
    run_ui(r'''
(async () => {
    nodes["benchmark-mode"].value = "deep";
    await nodes["btn-benchmark-start"].listeners.click();
    assert.equal(requests.length, 1);
    assert.equal(requests[0].path, "/settings/benchmark/start");
    assert.equal(requests[0].payload.mode, "deep");
    assert.equal(requests[0].payload.provider, "api");
    assert.equal(Array.from(requests[0].payload.models).join(","), "api:model-a,api:model-b");
    assert.equal(context.state.modelBenchmark.status, "running");
    await nodes["btn-benchmark-start"].listeners.click();
    assert.equal(requests.length, 1, "An active benchmark must not start again");
})().catch(error => { console.error(error); process.exitCode = 1; });
''')


def test_direct_model_actions_keep_running_empty_selection_save_failure_and_request_failure_guards():
    run_ui(r'''
(async () => {
    context.state.taskStatus = "running";
    nodes["btn-test-enabled-models"].listeners.click();
    await nodes["btn-benchmark-start"].listeners.click();
    await settle();
    assert.equal(requests.length, 0);
    context.state.taskStatus = "idle";
    allowSave = false;
    nodes["btn-test-enabled-nsfw"].listeners.click();
    await settle();
    assert.equal(requests.length, 0);
    allowSave = true;
    context.state.modelCatalog.api.forEach(model => model.enabled = false);
    nodes["btn-test-enabled-models"].listeners.click();
    await nodes["btn-benchmark-start"].listeners.click();
    await settle();
    assert.equal(requests.length, 0);
    context.state.modelCatalog.api[0].enabled = true;
    context.state.modelCatalog.api[1].enabled = true;
    failModel = "api:model-a";
    nodes["btn-test-enabled-models"].listeners.click();
    await settle();
    assert.equal(requests.length, 2);
    assert.equal(context.state.modelAvailability["api:model-a"].status, "unavailable");
    assert.equal(context.state.modelAvailability["api:model-b"].status, "available");
    assert.equal(context.state.settingsBusy, "");
    failBenchmark = true;
    await nodes["btn-benchmark-start"].listeners.click();
    assert.equal(requests.length, 3);
    assert.ok(notices.some(notice => notice.message === "benchmark request failed" && notice.kind === "error"));
})().catch(error => { console.error(error); process.exitCode = 1; });
''')


def test_benchmark_start_waiting_for_response_cannot_be_submitted_twice():
    run_ui(r'''
(async () => {
    let release;
    context.API.post = async (path, payload) => {
        requests.push({path, payload});
        await new Promise(resolve => { release = resolve; });
        return {status: "running"};
    };
    const first = nodes["btn-benchmark-start"].listeners.click();
    const second = nodes["btn-benchmark-start"].listeners.click();
    await settle();
    assert.equal(requests.length, 1);
    release();
    await Promise.all([first, second]);
    assert.equal(context.state.modelBenchmark.status, "running");
})().catch(error => { console.error(error); process.exitCode = 1; });
''')
