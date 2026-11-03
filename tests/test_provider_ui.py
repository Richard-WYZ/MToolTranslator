from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_provider_editor_isolates_new_connections_and_requires_explicit_switch():
    """Exercise provider edits without a browser, server, or live model requests."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the provider UI interaction check")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, "-"],
        cwd=root,
        input=r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const html = fs.readFileSync("ui/templates/index.html", "utf8");
assert.match(html, /<select id="settings-default-model">/);
const nodes = {};
for (const [, tag, id] of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"/g)) {
    nodes[id] = {
        tag, _value: "", _html: "", checked: false, disabled: false, hidden: false,
        get value() { return this._value; },
        set value(value) {
            this._value = this.tag === "select" && this._html && !this._html.includes('value="' + value + '"') ? "" : value;
        },
        get innerHTML() { return this._html; },
        set innerHTML(value) { this._html = value; this._value = (value.match(/value="([^"]*)"/) || [0, ""])[1]; },
    };
}
const original = {
    provider: "api", active_api_profile: "old", default_model: "api:old-model",
    api: { style: "openai", base_url: "https://old.example/v1", models: ["old-model"], api_key_configured: true, api_key_source: "env_file" },
    api_profiles: [
        { id: "old", name: "Old provider", preset: "openai" },
        { id: "saved", name: "Saved provider", preset: "openai" },
    ],
    provider_presets: [
        { id: "openai", name: "Custom Chat", style: "openai", base_url: "" },
        { id: "opendesign_go", name: "OpenDesign Go", style: "openai", base_url: "https://amr-link.open-design.ai/v1" },
        { id: "opencode_go", name: "OpenCode Go", style: "opencode_go", base_url: "https://opencode.example/v1" },
    ],
    ollama: { host: "http://localhost:11434" }, file: { writable: true },
};
const calls = [];
let benchmarkLoads = 0;
let runtimeLoads = 0;
const context = {
    state: { settings: original, modelCatalog: {api: [], ollama: []}, models: [], modelAvailability: {}, modelNsfwAvailability: {}, taskStatus: "idle" },
    el: id => { assert.ok(nodes[id], "Missing DOM node " + id); return nodes[id]; },
    all: () => [], escapeHtml: text => String(text),
    modelLabel: id => id.replace(/^(api|ollama):/, ""),
    modelTestRecord: () => ({ status: "untested" }), modelTestTimeSummary: () => "",
    benchmarkIsActive: () => false, renderModelBenchmark: () => {}, toast: () => {},
    loadPersistedModelStatuses: () => {}, invalidatePreflight: () => {},
    loadModelBenchmark: async () => {
        benchmarkLoads++;
        assert.equal(context.state.modelBenchmark, null, "Old-provider benchmark must be cleared before reloading");
        context.state.modelBenchmark = {profile_id: context.state.settings.active_api_profile};
    },
    API: { get: async path => {
        assert.equal(path, "/models");
        runtimeLoads++;
        return {models: [], runtime: {profile_id: context.state.settings.active_api_profile}};
    }, put: async (path, body) => {
        calls.push({path, body});
        return {settings: {...original, active_api_profile: "saved"}};
    }, post: async (path, body) => {
        calls.push({path, body});
        return {settings: {...original, active_api_profile: "saved"}};
    } },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync("ui/static/js/profiles.js", "utf8"), context);
context.configureProfiles = () => {};
context.populateModelSelectors = () => {};
context.renderProfile = () => {};
vm.runInContext(fs.readFileSync("ui/static/js/settings.js", "utf8"), context);
(async () => {
    context.initializeModelCatalog();
    context.renderSettings();
    assert.equal(nodes["settings-opencode-protocol"].hidden, true);
    assert.equal(nodes["settings-opencode-protocol"].disabled, true);
    nodes["settings-api-preset"].value = "opencode_go";
    context.updateApiProtocolOptions();
    assert.equal(nodes["settings-opencode-protocol"].hidden, false);
    assert.equal(nodes["settings-opencode-protocol"].disabled, false);
    nodes["settings-api-style"].value = "opencode_go";
    nodes["settings-api-preset"].value = "opendesign_go";
    context.updateApiProtocolOptions();
    assert.equal(nodes["settings-api-style"].value, "openai");
    assert.equal(nodes["settings-opencode-protocol"].disabled, true);
    assert.equal(nodes["settings-default-model"].value, "api:old-model");
    assert.ok(nodes["settings-default-model"].innerHTML.includes("old-model"));
    context.state.modelAvailability = {"api:old-model": {status: "available"}};
    context.newApiProvider();
    assert.notEqual(context.state.settingsApiProfileId, "old");
    assert.equal(nodes["settings-api-key"].value, "");
    assert.equal(context.state.modelCatalog.api.length, 0);
    assert.equal(Object.keys(context.state.modelAvailability).length, 0);
    assert.equal(nodes["settings-base-url"].value, "https://amr-link.open-design.ai/v1");
    assert.match(context.settingsValidationMessage(), /密钥/);
    nodes["settings-api-key"].value = "new-test-key";
    assert.equal(context.settingsValidationMessage(), "");
    const draft = context.settingsPayload();
    assert.equal(draft.default_model, "");
    assert.equal(draft.api_key_action, "replace");
    assert.equal(draft.api_models.length, 0);
    assert.equal(draft.api_preset, "opendesign_go");
    nodes["settings-api-profile"].value = "saved";
    await context.activateApiProvider();
    assert.equal(calls.length, 0, "Dirty provider edits must prevent activation");
    assert.deepEqual(original.api.models, ["old-model"], "Creating a provider must preserve the saved catalog");
    nodes["settings-model-manual"].value = "new-model";
    context.addSettingsModel();
    assert.equal(nodes["settings-default-model"].value, "api:new-model");
    assert.equal(context.settingsPayload().api_models[0], "new-model");
    context.state.settingsDirty = false;
    context.state.settingsConnectionDirty = false;
    context.initializeModelCatalog();
    context.renderSettings();
    nodes["settings-api-profile"].value = "saved";
    assert.equal(calls.length, 0, "Choosing a profile must not activate it");
    await context.activateApiProvider();
    assert.equal(calls.length, 1);
    assert.equal(calls[0].path, "/settings/providers/activate");
    assert.equal(calls[0].body.profile_id, "saved");
    assert.equal(context.state.settings.active_api_profile, "saved");
    assert.equal(benchmarkLoads, 1, "Provider activation must reload the benchmark");
    assert.equal(runtimeLoads, 1, "Provider activation must reload models and runtime routes");
    assert.equal(context.state.runtime.profile_id, "saved");
    assert.equal(context.state.modelBenchmark.profile_id, "saved");
    await context.saveSettings({preventDefault: () => {}});
    assert.equal(benchmarkLoads, 2, "Saving a provider must reload the benchmark");
    assert.equal(runtimeLoads, 2, "Saving a provider must reload models and runtime routes");
    assert.equal(calls[1].path, "/settings");
    context.state.settings = {...original, default_model: "api:", api: {...original.api, models: []}};
    context.initializeModelCatalog();
    assert.equal(context.state.modelCatalog.api.length, 0, "An empty connection must not acquire a blank model entry");
})().catch(error => { console.error(error); process.exitCode = 1; });
''',
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
