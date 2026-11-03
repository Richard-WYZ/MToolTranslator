from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


HARNESS = r'''
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const html = fs.readFileSync("ui/templates/index.html", "utf8"), nodes = {}, timers = new Map();
let timerId = 0;
for (const [, tag, id] of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"/g)) {
    nodes[id] = {tag, _value: "", _html: "", checked: false, disabled: false, hidden: false, listeners: {},
        classList: {toggle() {}}, addEventListener(event, listener) {this.listeners[event] = listener;},
        get value() {return this._value;},
        set value(value) {this._value = this.tag === "select" && this._html && !this._html.includes('value="' + value + '"') ? "" : value;},
        get innerHTML() {return this._html;},
        set innerHTML(value) {this._html = value; this._value = (value.match(/value="([^"]*)"/) || [0, ""])[1];},
    };
}
let settings = {provider: "api", active_api_profile: "saved", default_model: "api:model-a",
    api: {style: "openai", preset: "openai", base_url: "https://saved.invalid/v1", models: ["model-a", "model-b"], api_key_configured: true, protocol_mode: "auto", model_endpoints: {"model-a": ["responses"]}},
    api_profiles: [{id: "saved", name: "Saved", preset: "openai"}],
    provider_presets: [{id: "openai", name: "Custom", style: "openai", base_url: ""}, {id: "opendesign_go", name: "OpenDesign Go", style: "openai", base_url: "https://design.invalid/v1"}],
    ollama: {host: "http://localhost:11434"}, file: {writable: true}, model_test_statuses: {},
};
const secrets = new Map([["saved", "saved-test-key"]]), puts = [], posts = [], refreshes = [];
let customPut = null, customRefresh = null, failSave = false;
function persisted(payload) {
    const id = payload.api_profile_id;
    let secret = secrets.get(id) || "";
    if (payload.api_key_action === "replace") secret = payload.api_key;
    if (payload.api_key_action === "clear") secret = "";
    secrets.set(id, secret);
    const profile = {id, name: payload.api_profile_name, preset: payload.api_preset, api_key_configured: Boolean(secret)};
    const profiles = settings.api_profiles.filter(item => item.id !== id).concat(profile);
    settings = {...settings, provider: payload.provider, active_api_profile: id, default_model: payload.default_model,
        api_profiles: profiles, api: {...settings.api, style: payload.api_style, preset: payload.api_preset,
            base_url: payload.api_base_url, models: payload.api_models.slice(), disabled_models: payload.disabled_api_models.slice(),
            api_key_configured: Boolean(secret), protocol_mode: payload.api_protocol_mode,
            model_endpoints: structuredClone(payload.api_model_endpoints), disable_thinking: payload.disable_thinking},
        ollama: {host: payload.ollama_host, disabled_models: payload.disabled_ollama_models.slice()},
    };
    return {settings: structuredClone(settings)};
}
const context = {
    state: {settings: structuredClone(settings), settingsDirty: false, settingsConnectionDirty: false, taskStatus: "idle",
        models: [], modelCatalog: {api: [], ollama: []}, modelAvailability: {}, modelNsfwAvailability: {}, runtime: null},
    el: id => nodes[id] || null, all: () => [], escapeHtml: String,
    modelLabel: model => String(model || "").replace(/^(api|ollama):/, ""),
    modelTestRecord: () => ({status: "untested"}), modelTestTimeSummary: () => "",
    benchmarkIsActive: () => false, renderModelBenchmark() {}, bindModelBenchmarkEvents() {},
    loadPersistedModelStatuses() {}, invalidatePreflight() {}, toast() {},
    setTimeout: (callback, delay) => {const id = ++timerId; timers.set(id, {callback, delay}); return id;},
    clearTimeout: id => timers.delete(id), window: {confirm: () => true},
    loadModels: async () => {
        if (context.state.settingsAutosaving) assert.equal(context.state.settingsDirty, true, "Autosave runtime refresh must preserve the settings editor");
        if (customRefresh) await customRefresh();
        context.state.runtime = {default_model: settings.default_model, provider_name: settings.api_profiles.at(-1).name};
        refreshes.push(settings.default_model);
    },
    loadModelBenchmark: async () => {context.state.modelBenchmark = {status: "idle"};},
    API: {
        put: async (path, payload) => {
            assert.equal(path, "/settings");
            const snapshot = structuredClone(payload); puts.push(snapshot);
            if (failSave) throw new Error("disk unavailable");
            if (customPut) await customPut(snapshot);
            return persisted(snapshot);
        },
        post: async (path, payload) => {
            posts.push({path, payload: structuredClone(payload), savedKey: secrets.get(settings.active_api_profile)});
            if (path.endsWith("/discover")) return {ok: true, models: [{name: "model-a", supported_endpoint_types: ["responses"]}, {name: "model-b", supported_endpoint_types: ["chat_completions"]}]};
            return {ok: true, nsfw_supported: true, test_status: {status: "available"}};
        },
    },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync("ui/static/js/settings.js", "utf8"), context);
vm.runInContext(fs.readFileSync("ui/static/js/settings-autosave.js", "utf8"), context);
vm.runInContext(fs.readFileSync("ui/static/js/model-testing.js", "utf8"), context);
context.initializeModelCatalog(); context.renderSettings(); context.bindSettingsEvents();
function input(id, value) {nodes[id].value = value; nodes[id].listeners.input.call(nodes[id]);}
function change(id, value) {if (value !== undefined) nodes[id].value = value; nodes[id].listeners.change.call(nodes[id]);}
'''


def run_ui(script: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for settings interaction tests")
    result = subprocess.run(
        [node, "-"], cwd=Path(__file__).resolve().parents[1], input=HARNESS + "\n(async () => {\n" + script
        + "\n})().catch(error => { console.error(error); process.exitCode = 1; });\n",
        text=True, encoding="utf-8", capture_output=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_ordinary_edits_autosave_without_sending_or_erasing_key_drafts():
    run_ui(r'''
input("settings-api-key", "unconfirmed-test-key");
assert.equal(timers.size, 0, "Key input must not schedule a save");
input("settings-profile-name", "New name");
assert.equal(Array.from(timers.values())[0].delay, 350);
input("settings-profile-name", "Newest name");
assert.equal(timers.size, 1);
assert.equal(await context.flushSettingsAutosave(), true);
assert.equal(puts.length, 1);
assert.equal(puts[0].api_profile_name, "Newest name");
assert.equal(puts[0].api_key_action, "keep");
assert.ok(!("api_key" in puts[0]));
assert.ok(!("api_model_protocols" in puts[0]));
assert.equal(puts[0].api_protocol_mode, "auto");
assert.deepEqual(puts[0].api_model_endpoints, {"model-a": ["responses"]});
assert.equal(secrets.get("saved"), "saved-test-key");
assert.equal(nodes["settings-api-key"].value, "unconfirmed-test-key");
assert.equal(posts.length, 0, "Autosaving ordinary settings must never test or invoke models");
assert.equal(context.state.runtime.provider_name, "Newest name");
assert.equal(nodes["settings-key-state"].textContent, "待确认保存");
''')


def test_autosave_serializes_newest_input_and_keeps_fields_editable():
    run_ui(r'''
let release, inFlight = 0, maximum = 0;
customPut = async () => {
    maximum = Math.max(maximum, ++inFlight);
    if (puts.length === 1) await new Promise(resolve => {release = resolve;});
    inFlight--;
};
input("settings-profile-name", "First name");
const flushing = context.flushSettingsAutosave();
await new Promise(resolve => setImmediate(resolve));
assert.equal(nodes["settings-fieldset"].disabled, false);
input("settings-profile-name", "Latest name");
input("settings-api-key", "pending-key");
release();
assert.equal(await flushing, true);
assert.equal(maximum, 1);
assert.deepEqual(puts.map(payload => payload.api_profile_name), ["First name", "Latest name"]);
assert.equal(nodes["settings-profile-name"].value, "Latest name");
assert.equal(nodes["settings-api-key"].value, "pending-key");
assert.equal(context.state.settings.api_profiles.at(-1).name, "Latest name");
assert.equal(context.state.runtime.provider_name, "Latest name");
assert.ok(puts.every(payload => payload.api_key_action === "keep" && !("api_key" in payload)));
''')


def test_failed_autosave_preserves_inputs_and_retry_uses_the_latest_value():
    run_ui(r'''
failSave = true;
input("settings-base-url", "https://changed.invalid/v1");
assert.equal(await context.flushSettingsAutosave(), false);
assert.equal(nodes["settings-base-url"].value, "https://changed.invalid/v1");
assert.equal(nodes["btn-settings-retry"].hidden, false);
assert.match(nodes["settings-save-note"].textContent, /失败/);
assert.equal(context.state.settings.api.base_url, "https://saved.invalid/v1");
input("settings-profile-name", "Retry newest");
failSave = false;
assert.equal(await context.flushSettingsAutosave(), true);
assert.equal(puts.at(-1).api_profile_name, "Retry newest");
assert.equal(puts.at(-1).api_base_url, "https://changed.invalid/v1");
assert.equal(nodes["btn-settings-retry"].hidden, true);
assert.equal(context.state.settingsDirty, false);
''')


def test_preset_changes_during_an_inflight_save_are_queued_without_key_inheritance():
    run_ui(r'''
let release;
customPut = async () => {if (puts.length === 1) await new Promise(resolve => {release = resolve;});};
input("settings-profile-name", "Updated old profile");
const flushing = context.flushSettingsAutosave();
await new Promise(resolve => setImmediate(resolve));
nodes["settings-api-preset"].value = "opendesign_go";
context.applyProviderPreset(false);
const newId = context.state.settingsApiProfileId;
input("settings-api-key", "unconfirmed-new-key");
release();
assert.equal(await flushing, true);
assert.equal(puts.length, 2);
assert.equal(puts[0].api_profile_id, "saved");
assert.equal(puts[1].api_profile_id, newId);
assert.equal(context.state.settings.active_api_profile, newId);
assert.equal(secrets.get(newId), "");
assert.equal(secrets.get("saved"), "saved-test-key");
assert.equal(nodes["settings-api-key"].value, "unconfirmed-new-key");
assert.ok(puts.every(payload => payload.api_key_action === "keep" && !("api_key" in payload)));
''')


def test_new_provider_ordinary_fields_upsert_without_inheriting_a_key():
    run_ui(r'''
context.newApiProvider();
const id = context.state.settingsApiProfileId;
assert.notEqual(id, "saved");
assert.equal(await context.flushSettingsAutosave(), true);
assert.equal(puts[0].api_profile_id, id);
assert.equal(puts[0].api_key_action, "keep");
assert.equal(puts[0].api_models.length, 0);
assert.equal(puts[0].default_model, "api:");
assert.ok(!("api_key" in puts[0]));
assert.equal(secrets.get(id), "");
assert.equal(secrets.get("saved"), "saved-test-key");
input("settings-profile-name", "New connection name");
input("settings-api-key", "new-provider-test-key");
assert.equal(await context.flushSettingsAutosave(), true);
assert.equal(secrets.get(id), "");
assert.equal(nodes["settings-api-key"].value, "new-provider-test-key");
assert.equal(await context.saveSettings({preventDefault() {}}), true);
assert.equal(puts.at(-1).api_key_action, "replace");
assert.equal(secrets.get(id), "new-provider-test-key");
''')


def test_clear_and_replace_keys_require_the_explicit_save_action():
    run_ui(r'''
input("settings-api-key", "replacement-test-key");
assert.equal(await context.flushSettingsAutosave(), true);
assert.equal(puts.length, 0);
assert.equal(await context.saveSettings({preventDefault() {}}), true);
assert.equal(secrets.get("saved"), "replacement-test-key");
nodes["settings-clear-key"].checked = true;
change("settings-clear-key");
assert.equal(await context.flushSettingsAutosave(), true);
assert.equal(puts.length, 1);
input("settings-profile-name", "Name while clearing");
assert.equal(await context.flushSettingsAutosave(), true);
assert.equal(puts.at(-1).api_key_action, "keep");
assert.equal(secrets.get("saved"), "replacement-test-key");
assert.equal(nodes["settings-clear-key"].checked, true);
assert.equal(await context.saveSettings({preventDefault() {}}), true);
assert.equal(puts.at(-1).api_key_action, "clear");
assert.equal(secrets.get("saved"), "");
''')


def test_tests_flush_ordinary_edits_and_use_the_saved_key_and_auto_protocol():
    run_ui(r'''
input("settings-api-key", "not-confirmed-key");
input("settings-base-url", "https://new-address.invalid/v1");
await context.testSingleModel("api:model-a", "adult");
assert.equal(puts.length, 1);
assert.equal(puts[0].api_key_action, "keep");
assert.equal(posts.length, 1);
assert.equal(posts[0].path, "/settings/connection-test");
assert.equal(posts[0].savedKey, "saved-test-key");
assert.equal(posts[0].payload.protocol, "auto");
assert.equal(posts[0].payload.test_kind, "adult");
assert.equal(nodes["settings-api-key"].value, "not-confirmed-key");
assert.ok(!context.renderModelRow(context.state.modelCatalog.api[0]).includes("data-model-protocol"));
''')


def test_flush_waits_for_runtime_model_refresh_before_translation_can_start():
    run_ui(r'''
let releaseRefresh, finished = false;
customRefresh = () => new Promise(resolve => {releaseRefresh = resolve;});
change("settings-default-model", "api:model-b");
assert.equal(Array.from(timers.values())[0].delay, 0);
const flushing = context.flushSettingsAutosave().then(result => {finished = true; return result;});
await new Promise(resolve => setImmediate(resolve));
assert.equal(finished, false);
releaseRefresh();
assert.equal(await flushing, true);
assert.equal(context.state.runtime.default_model, "api:model-b");
assert.equal(context.state.settings.default_model, "api:model-b");
assert.equal(nodes["settings-default-model"].value, "api:model-b");
''')
