"use strict";

var settingsAutosaveTimer = null;
var settingsAutosavePromise = null;
var settingsEditVersion = 0;
var settingsSavedVersion = 0;
var settingsAutosaveError = "";

function settingsHasSecretEdits() {
    return Boolean(el("settings-api-key").value.trim() || el("settings-clear-key").checked);
}

function settingsProfileExists() {
    return Boolean(state.settings && (state.settings.api_profiles || []).some(function (profile) {
        return profile.id === state.settingsApiProfileId;
    }));
}

function syncSettingsDirtyState() {
    state.settingsDirty = settingsEditVersion > settingsSavedVersion || settingsHasSecretEdits()
        || Boolean(state.settings && !settingsProfileExists());
}

function resetSettingsAutosave() {
    clearTimeout(settingsAutosaveTimer);
    settingsEditVersion = 0;
    settingsSavedVersion = 0;
    settingsAutosaveError = "";
    state.settingsAutosaving = false;
    state.settingsSecretError = "";
}

function settingsAutosaveValidation() {
    if (!state.settings) return "设置尚未加载";
    if (!(state.settings.file || {}).writable) return "配置目录不可写；修改仍保留在界面。";
    var provider = currentSettingsProvider(), model = el("settings-default-model").value;
    if (currentModelCatalog().length && !currentModelCatalog().some(function (item) { return item.enabled && item.id === model; })) {
        return "请选择一个已启用的默认模型。";
    }
    if (provider === "api" && !el("settings-profile-name").value.trim()) return "请填写提供商配置名称。";
    var endpoint = provider === "api" ? el("settings-base-url").value.trim() : el("settings-ollama-host").value.trim();
    if (!endpoint && provider === "api") {
        var preset = (state.settings.provider_presets || []).find(function (item) { return item.id === el("settings-api-preset").value; });
        endpoint = preset && preset.base_url || "";
    }
    if (!endpoint || !/^https?:\/\/[^\s/]+(?:\/[^\s]*)?$/i.test(endpoint)) return "请填写完整的 HTTP(S) 服务地址。";
    return "";
}

function scheduleSettingsAutosave(immediate) {
    settingsEditVersion += 1;
    settingsAutosaveError = "";
    syncSettingsDirtyState();
    clearTimeout(settingsAutosaveTimer);
    settingsAutosaveTimer = setTimeout(function () { flushSettingsAutosave(); }, immediate ? 0 : 350);
}

async function refreshSettingsRuntime() {
    // Model refresh must not re-render the settings form while an unconfirmed
    // key or a newer edit is present. Only the saved runtime is refreshed.
    state.settingsDirty = true;
    try {
        await loadModels(false);
        if (typeof loadModelBenchmark === "function") await loadModelBenchmark();
        renderSettingsStatus();
        invalidatePreflight();
    } finally { syncSettingsDirtyState(); }
}

async function flushSettingsAutosave() {
    clearTimeout(settingsAutosaveTimer);
    if (!state.settings) return true;
    if (settingsAutosavePromise) return settingsAutosavePromise;
    if (settingsEditVersion <= settingsSavedVersion) return true;
    if (settingsTaskIsActive() || benchmarkIsActive() || state.settingsBusy) {
        settingsAutosaveError = "当前任务进行中，修改尚未保存。";
        updateSettingsControls();
        return false;
    }
    settingsAutosavePromise = (async function () {
        state.settingsAutosaving = true;
        updateSettingsControls();
        try {
            do {
                while (settingsSavedVersion < settingsEditVersion) {
                    var validation = settingsAutosaveValidation();
                    if (validation) { settingsAutosaveError = validation; return false; }
                    var version = settingsEditVersion;
                    // Ordinary persistence never includes replacement keys or
                    // the pending clear checkbox, even when they are edited.
                    var result = await API.put("/settings", settingsPayload(false));
                    state.settings = result.settings;
                    settingsSavedVersion = version;
                    settingsAutosaveError = "";
                    if (version === settingsEditVersion) state.settingsConnectionDirty = false;
                    loadPersistedModelStatuses();
                    if (version === settingsEditVersion) {
                        renderSettingsModelPicker();
                        el("settings-api-profile").innerHTML = (state.settings.api_profiles || []).map(function (profile) {
                            return '<option value="' + escapeHtml(profile.id) + '">' + escapeHtml(profile.name)
                                + (profile.id === state.settings.active_api_profile ? " · 当前启用" : "") + "</option>";
                        }).join("");
                        el("settings-api-profile").value = state.settingsApiProfileId;
                        renderActiveProviderNote();
                    }
                    syncSettingsDirtyState();
                }
                state.modelBenchmark = null;
                await refreshSettingsRuntime();
            } while (settingsSavedVersion < settingsEditVersion);
            return true;
        } catch (error) {
            settingsAutosaveError = "自动保存失败，修改仍保留：" + error.message;
            return false;
        } finally {
            state.settingsAutosaving = false;
            syncSettingsDirtyState();
            updateSettingsControls();
            renderModelBenchmark();
        }
    })();
    try { return await settingsAutosavePromise; }
    finally { settingsAutosavePromise = null; }
}
