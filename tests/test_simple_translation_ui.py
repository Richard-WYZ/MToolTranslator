from pathlib import Path
import shutil
import subprocess

import pytest


def run_ui_script(script: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for interactive UI checks")
    result = subprocess.run(
        [node, "-"], input=script, text=True, encoding="utf-8", capture_output=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_one_click_start_prepares_once_and_keeps_project_on_switch():
    run_ui_script(r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const nodes = {};
const el = id => nodes[id] ||= {textContent:'',value:'',hidden:false,disabled:false,open:false,style:{},dataset:{},classList:{toggle(){}}};
let finishPreparation;
let blocked = false;
const calls = [];
const context = {
    state:{filePath:'game.json',profile:'single_model',taskStatus:'idle',startPreparing:false,preflight:null,hasUnexportedResult:true,review:{}},
    el, all:()=>[], window:{}, aiReviewIsActive:()=>false, flushReviewDraft:async()=>true,
    selectedModel:()=> 'api:chosen', profileOptions:()=>({protocol:'json'}), toast(){},
    API:{async post(path, payload) {
        calls.push({path,payload});
        if (path === '/preflight') {
            if (!finishPreparation && !blocked) await new Promise(resolve => finishPreparation = resolve);
            return {ok:!blocked};
        }
        if (path === '/translate/start') return {task_id:'one-task'};
        throw new Error('Unexpected request: '+path);
    }},
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('ui/static/js/translate.js','utf8'),context);
context.renderPreflight=()=>{};
context.startPolling=()=>{};
(async()=>{
    // No manually generated preflight exists; double click still creates one task.
    const first=context.startTranslation();
    const second=context.startTranslation();
    await new Promise(setImmediate);
    assert.equal(calls.filter(c=>c.path==='/preflight').length,1);
    finishPreparation();
    await Promise.all([first,second]);
    assert.equal(calls.filter(c=>c.path==='/translate/start').length,1);
    assert.equal(context.state.taskId,'one-task');
    assert.equal(calls[1].payload.model,'api:chosen');
    assert.equal(await context.ensureCanReplaceFile(),false);
    context.state.pollingGeneration=1;
    context.API.get=async()=>({status:'completed',snapshot_ready:true});
    context.renderProgress=()=>{};
    context.refreshVisibleWorkspaceDuringTranslation=()=>{};
    context.refreshReviewCount=async()=>{};
    await context.pollProgress(1);
    assert.equal(nodes['btn-export'].disabled,false);
    context.state.taskStatus='idle';
    assert.equal(await context.ensureCanReplaceFile(),true);
    assert.equal(calls.length,2); // Switching never deletes the old working copy.
    blocked=true;
    await context.startTranslation();
    assert.equal(context.state.taskStatus,'idle');
    assert.equal(context.state.startPreparing,false);
    assert.equal(nodes['preflight-panel'].open,true);
    assert.equal(calls.filter(c=>c.path==='/translate/start').length,1);
    blocked=false;
    await context.startTranslation();
    assert.equal(calls.filter(c=>c.path==='/translate/start').length,2);
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


def test_export_defaults_to_save_as_without_cleanup_prompts():
    run_ui_script(r'''
const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const calls=[];
const context={
    state:{sessionId:'session',filePath:'work.json',fileName:'game.json',originalFilePath:'original.json'},
    window:{pywebview:{api:{async save_file_dialog(){return 'exported.json';}}},confirm(){throw new Error('Unexpected confirmation');}},
    el:()=>({disabled:false}), toast(){}, updateActionStates(){}, flushReviewDraft:async()=>true,
    API:{async post(path,payload){calls.push({path,payload});return path==='/export' ? {export_path:'exported.json',finalization:{confirmation_required:true,cleanup_token:'receipt'}} : {cleaned:false};}},
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('ui/static/js/export.js','utf8'),context);
(async()=>{
    await context.exportResult();
    assert.equal(calls[0].path,'/export');
    assert.equal(calls[0].payload.overwrite_original,false);
    assert.equal(calls[0].payload.output_path,'exported.json');
    assert.equal(calls[1].payload.cleanup,false);
    assert.equal(context.state.filePath,'work.json');
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


def test_overwrite_button_exports_directly_without_a_second_confirmation():
    run_ui_script(r'''
const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const calls=[];
const context={
    state:{sessionId:'session',filePath:'work.json',fileName:'game.json',originalFilePath:'original.json'},
    window:{pywebview:{api:{save_file_dialog(){throw new Error('Overwrite must not ask for a destination');}}},confirm(){throw new Error('Unexpected confirmation');}},
    el:()=>({disabled:false}), toast(){}, updateActionStates(){}, flushReviewDraft:async()=>true,
    API:{async post(path,payload){calls.push({path,payload});return {export_path:'original.json',overwrote_original:true};}},
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('ui/static/js/export.js','utf8'),context);
(async()=>{
    await context.exportResult(true);
    assert.equal(calls.length,1);
    assert.equal(calls[0].path,'/export');
    assert.equal(calls[0].payload.overwrite_original,true);
    assert.equal(calls[0].payload.file_path,'work.json');
    assert.equal(calls[0].payload.output_path,null);
    assert.equal(context.state.exportBusy,false);
})().catch(error=>{console.error(error);process.exitCode=1;});
''')


def test_export_double_click_uses_original_project_snapshot():
    run_ui_script(r'''
const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
let choosePath, dialogs=0;
const calls=[];
const context={
    state:{sessionId:'first',filePath:'first.json',fileName:'first.json',hasUnexportedResult:true},
    window:{pywebview:{api:{save_file_dialog(){dialogs++;return new Promise(resolve=>choosePath=resolve);}}}},
    el:()=>({disabled:false}), toast(){}, updateActionStates(){}, flushReviewDraft:async()=>true,
    API:{async post(path,payload){calls.push({path,payload});return {export_path:'export.json'};}},
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('ui/static/js/export.js','utf8'),context);
(async()=>{
    const first=context.exportResult();
    const duplicate=context.exportResult();
    await new Promise(setImmediate);
    assert.equal(dialogs,1);
    context.state.filePath='another.json';
    context.state.sessionId='another';
    choosePath('export.json');
    await Promise.all([first,duplicate]);
    assert.equal(calls.length,1);
    assert.equal(calls[0].payload.file_path,'first.json');
    assert.equal(calls[0].payload.session_id,'first');
    assert.equal(context.state.hasUnexportedResult,true);
    assert.equal(context.state.exportBusy,false);
})().catch(error=>{console.error(error);process.exitCode=1;});
''')
