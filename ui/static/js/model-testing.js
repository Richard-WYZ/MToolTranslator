"use strict";

function modelTestRecord(store, modelId) {
    var value = store[modelId];
    if (typeof value === "string") return { status: value, tested_at: "", stale: false };
    return value || { status: "untested", tested_at: "", stale: false };
}

function loadPersistedModelStatuses() {
    state.modelAvailability = {};
    state.modelNsfwAvailability = {};
    Object.entries((state.settings || {}).model_test_statuses || {}).forEach(function (entry) {
        if (entry[1].basic) state.modelAvailability[entry[0]] = entry[1].basic;
        if (entry[1].adult) state.modelNsfwAvailability[entry[0]] = entry[1].adult;
    });
}

function modelTestTime(record) {
    if (!record.tested_at) return "";
    var value = new Date(record.tested_at);
    return Number.isNaN(value.getTime()) ? record.tested_at : value.toLocaleString("zh-CN", {
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", hour12: false,
    });
}

function modelTestTimeSummary(basic, adult) {
    var parts = [];
    if (basic.tested_at) parts.push("普通上次测试 " + modelTestTime(basic));
    if (adult.tested_at) parts.push("NSFW 上次测试 " + modelTestTime(adult));
    return parts.join(" · ") || "尚无历史测试";
}

function enabledModelsForCurrentProvider() {
    return currentModelCatalog().filter(function (item) { return item.enabled; });
}

async function requestModelTest(modelId, testKind) {
    return API.post("/settings/connection-test", {
        provider: modelId.split(":", 1)[0],
        model: modelId,
        test_kind: testKind,
    });
}

async function testSingleModel(modelId, testKind) {
    if (state.settingsConnectionDirty) {
        toast("请先保存连接参数，再测试模型", "error");
        return;
    }
    var adult = testKind === "adult";
    var statusStore = adult ? state.modelNsfwAvailability : state.modelAvailability;
    statusStore[modelId] = { status: "testing", tested_at: "", stale: false };
    setSettingsBusy("testing");
    renderSettingsModelPicker();
    try {
        var result = await requestModelTest(modelId, testKind);
        statusStore[modelId] = result.test_status;
        var supported = !adult || result.nsfw_supported;
        toast(
            modelLabel(modelId) + (adult
                ? supported ? "：NSFW 可用" : "：NSFW 受限"
                : " 可用") + "；本次测试可能产生少量 Token",
            supported ? "success" : "error"
        );
    } catch (error) {
        statusStore[modelId] = {
            status: adult ? "error" : "unavailable",
            tested_at: new Date().toISOString(),
            stale: false,
        };
        toast(modelLabel(modelId) + " 测试失败：" + error.message, "error");
    } finally {
        setSettingsBusy("");
        renderSettingsModelPicker();
    }
}

function testModelAvailability(modelId) {
    return testSingleModel(modelId, "basic");
}

function testModelNsfwAvailability(modelId) {
    return testSingleModel(modelId, "adult");
}

async function testEnabledModels(testKind) {
    if (state.settingsConnectionDirty) {
        toast("请先保存连接参数，再测试模型", "error");
        return;
    }
    var models = enabledModelsForCurrentProvider();
    if (!models.length) {
        toast("请先勾选至少一个模型", "error");
        return;
    }
    var adult = testKind === "adult";
    var description = adult ? "成人内容兼容性" : "普通连接可用性";
    if (!window.confirm(
        "将逐个测试当前提供方已勾选的 " + models.length + " 个模型的" + description
        + "。每个模型都会发送一条请求并可能消耗 Token，是否继续？"
    )) return;

    var statusStore = adult ? state.modelNsfwAvailability : state.modelAvailability;
    models.forEach(function (item) {
        statusStore[item.id] = { status: "testing", tested_at: "", stale: false };
    });
    setSettingsBusy("testing");
    renderSettingsModelPicker();
    var passed = 0;
    for (var item of models) {
        try {
            var result = await requestModelTest(item.id, testKind);
            var supported = !adult || result.nsfw_supported;
            statusStore[item.id] = result.test_status || {
                status: supported ? "available" : "restricted",
                tested_at: new Date().toISOString(),
                stale: false,
            };
            if (supported) passed += 1;
        } catch (error) {
            statusStore[item.id] = {
                status: adult ? "error" : "unavailable",
                tested_at: new Date().toISOString(),
                stale: false,
            };
        }
        renderSettingsModelPicker();
    }
    setSettingsBusy("");
    renderSettingsModelPicker();
    toast(
        description + "测试完成：" + passed + " / " + models.length + " 通过",
        passed === models.length ? "success" : "error"
    );
}
