"use strict";

var API = {
    async request(method, path, body) {
        var options = { method: method, headers: {} };
        if (body !== undefined) {
            options.headers["Content-Type"] = "application/json";
            options.body = JSON.stringify(body);
        }
        var response;
        try {
            response = await fetch("/api" + path, options);
        } catch (error) {
            throw new Error("无法连接到本机翻译服务");
        }
        if (!response.ok) {
            var payload = await response.json().catch(function () { return {}; });
            throw new Error(payload.detail || ("请求失败（HTTP " + response.status + "）"));
        }
        return response.json();
    },
    get: function (path) { return this.request("GET", path); },
    post: function (path, body) { return this.request("POST", path, body); },
    put: function (path, body) { return this.request("PUT", path, body); },
    delete: function (path) { return this.request("DELETE", path); },
    upload: async function (file) {
        var data = new FormData();
        data.append("file", file);
        var response = await fetch("/api/import", { method: "POST", body: data });
        if (!response.ok) {
            var payload = await response.json().catch(function () { return {}; });
            throw new Error(payload.detail || "文件导入失败");
        }
        return response.json();
    },
};

var PAGE_META = {
    translate: ["TRANSLATION WORKSPACE", "翻译任务"],
    review: ["QUALITY REVIEW", "质量复核"],
    glossary: ["DYNAMIC TERMINOLOGY", "术语库"],
    history: ["TASK CONTINUITY", "历史与恢复"],
    settings: ["RUNTIME CONFIGURATION", "设置"],
};

var state = {
    page: "translate",
    models: [],
    runtime: null,
    filePath: "",
    sourceFilePath: "",
    fileName: "",
    fileSize: 0,
    sessionId: "",
    totalRows: 0,
    preview: null,
    profile: "quality_first",
    preflight: null,
    taskId: "",
    taskStatus: "idle",
    pollingTimer: null,
    hasUnexportedResult: false,
    review: {
        stats: null,
        filter: "issues",
        offset: 0,
        limit: 30,
        total: 0,
        items: [],
        selectedRow: null,
        selectedRows: new Set(),
    },
    glossary: {
        payload: { terms: {}, candidates: {} },
        filter: "all",
        search: "",
    },
    settings: null,
    settingsDirty: false,
    settingsConnectionDirty: false,
    settingsBusy: "",
    modelCatalog: { api: [], ollama: [] },
    modelAvailability: {},
    modelNsfwAvailability: {},
};

function el(id) { return document.getElementById(id); }
function all(selector, root) { return Array.from((root || document).querySelectorAll(selector)); }
function escapeHtml(value) {
    return String(value == null ? "" : value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}
function truncate(value, length) {
    var text = String(value || "").replace(/\s+/g, " ").trim();
    return text.length > length ? text.slice(0, length) + "…" : text;
}
function formatSize(bytes) {
    if (!bytes) return "0 KB";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1024 / 1024).toFixed(1) + " MB";
}
function formatDuration(seconds) {
    if (seconds == null || !isFinite(seconds)) return "—";
    seconds = Math.max(0, Math.round(seconds));
    var hours = Math.floor(seconds / 3600);
    var minutes = Math.floor((seconds % 3600) / 60);
    var remain = seconds % 60;
    if (hours) return hours + "时 " + minutes + "分";
    if (minutes) return minutes + "分 " + remain + "秒";
    return remain + "秒";
}
function formatDate(value) {
    if (!value) return "时间未知";
    var date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
    return isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", { hour12: false });
}
function modelLabel(modelId) {
    return String(modelId || "").replace(/^api:/, "").replace(/^ollama:/, "");
}
function toast(message, type) {
    var node = document.createElement("div");
    node.className = "toast " + (type || "");
    node.textContent = message;
    el("toast-region").appendChild(node);
    setTimeout(function () { node.remove(); }, 4200);
}

function navigate(page) {
    if (!PAGE_META[page]) page = "translate";
    if (location.hash !== "#" + page) location.hash = page;
    showPage(page);
}

function showPage(page) {
    if (!PAGE_META[page]) page = "translate";
    state.page = page;
    all(".page").forEach(function (node) { node.classList.toggle("active", node.id === "page-" + page); });
    all(".nav-list a").forEach(function (node) { node.classList.toggle("active", node.dataset.page === page); });
    el("page-eyebrow").textContent = PAGE_META[page][0];
    el("page-title").textContent = PAGE_META[page][1];
    if (page === "review") loadReview();
    if (page === "glossary") loadGlossary();
    if (page === "history") loadHistory();
    if (page === "settings" && !state.settings) loadSettings();
}

async function checkServer() {
    try {
        var response = await fetch("/health");
        if (!response.ok) throw new Error();
        el("server-dot").className = "status-dot online";
        el("server-status").textContent = "翻译服务运行中";
    } catch (error) {
        el("server-dot").className = "status-dot offline";
        el("server-status").textContent = "翻译服务不可用";
    }
}
