from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_first_render_does_not_wait_for_local_discovery_or_scan_history():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed for the offline startup interaction test")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, "-"], cwd=root, text=True, capture_output=True, encoding="utf-8", timeout=20,
        input=r'''
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
const calls=[],pending=[],timers=[],nodes={};
let resolveHealth,settingsRenders=0,renders=0;
const context={
    state:{page:"translate",models:[],runtime:null,settings:null,settingsDirty:false},
    el:id=>nodes[id]||(nodes[id]={textContent:""}),
    document:{readyState:"loading",addEventListener(){}},window:{},location:{hash:""},
    API:{get:path=>{calls.push(path);return new Promise(resolve=>pending.push({path,resolve}));}},
    checkServer:()=>{calls.push("health");return new Promise(resolve=>resolveHealth=resolve);},
    loadRecoveryBanner:()=>{throw Error("Startup must not scan recovery checkpoints");},
    initReviewWorkspace(){},initGlossaryWorkspace(){},initHistoryWorkspace(){},renderFile(){},resetTaskDisplay(){},showPage(){},
    navigate(){},loadReview(){},loadGlossary(){},reviewHasPendingDraft:()=>false,
    setTimeout:(fn,ms)=>{timers.push({fn,ms});return timers.length;},clearTimeout(){},toast(){},
};
vm.createContext(context);
vm.runInContext(fs.readFileSync("ui/static/js/profiles.js","utf8"),context);
vm.runInContext(fs.readFileSync("ui/static/js/main.js","utf8"),context);
context.bindEvents=()=>{};context.configureProfiles=()=>{};context.renderProfile=()=>renders++;
context.renderSettings=()=>settingsRenders++;
const settle=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
    context.init();
    assert.deepEqual(calls,["health","/models"]);
    assert.equal(context.loadModels(false),context.loadModels(false),"Concurrent configured reads must share one request");
    assert.equal(calls.filter(path=>path==="/models").length,1);
    pending.shift().resolve({models:[{id:"api:first"}],runtime:{}});
    await settle();
    assert.equal(renders,1,"Configured models must render before local discovery returns");
    assert.ok(calls.includes("/models?discover_local=true&refresh=false"));
    assert.equal(pending.length,1,"Local discovery remains optional background work");
    context.refreshStartupHealth();
    assert.equal(calls.filter(path=>path==="health").length,1,"Health polling must not overlap");
    resolveHealth();await settle();
    assert.equal(timers.filter(timer=>timer.ms===30000).length,1);
    context.state.settings={active_api_profile:"new",api:{models:["new"]}};
    context.state.settingsDirty=true;
    const reload=context.loadModels(false);
    const current=pending.pop();
    current.resolve({models:[{id:"api:new"}],runtime:{}});
    await reload;
    assert.equal(context.state.models[0].id,"api:new");
    assert.equal(settingsRenders,0,"Background model reads must preserve unsaved settings inputs");
    pending.shift().resolve({models:[{id:"api:old"}],runtime:{}});
    await settle();
    assert.notEqual(context.state.models[0].id,"api:old","An older provider response must not replace the active catalog");
    // The discarded request schedules a current configured read, never a second local probe.
    if(pending.length)pending.shift().resolve({models:[{id:"api:new"}],runtime:{}});
    await settle();
    assert.equal(calls.filter(path=>path.includes("discover_local=true")).length,1);
    const key=context.modelCatalogContext();
    context.state.settings.api.protocol_mode="auto";
    assert.notEqual(context.modelCatalogContext(),key);
    const protocolKey=context.modelCatalogContext();
    context.state.settings.api.model_endpoints={new:["responses"]};
    assert.notEqual(context.modelCatalogContext(),protocolKey);
    context.state.settings.ollama={host:"http://old.invalid",disabled_models:[]};
    const hostKey=context.modelCatalogContext();
    context.state.settings.ollama.host="http://new.invalid";
    assert.notEqual(context.modelCatalogContext(),hostKey);
    const enabledKey=context.modelCatalogContext();
    context.state.settings.ollama.disabled_models=["local"];
    assert.notEqual(context.modelCatalogContext(),enabledKey);
})().catch(error=>{console.error(error);process.exitCode=1;});
''',
    )
    assert result.returncode == 0, result.stdout + result.stderr
