from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_term_editor_requires_current_preview_and_keeps_examples_safe():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed for the offline glossary interaction test")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, "-"], cwd=root, text=True, capture_output=True, encoding="utf-8", timeout=20,
        input=r'''
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const html = fs.readFileSync("ui/templates/index.html", "utf8"), nodes = {};
for (const [, id] of html.matchAll(/\bid="([^"]+)"/g)) {
    nodes[id] = {value:"",textContent:"",innerHTML:"",hidden:false,disabled:false,events:{},
        setAttribute(){}, addEventListener(type,fn){this.events[type]=fn;}};
}
let payload = {terms:{},candidates:{"アリス":{status:"candidate",targets:{"爱丽丝":3},evidence:["speaker_position","manual_confirmed"]}}};
const calls = [], navigated = [];
const context = {
    state:{glossary:{payload,search:"",filter:"all"}}, activeFilePath:()=>"project.json",
    el:id=>{assert.ok(nodes[id],id);return nodes[id];},
    escapeHtml:text=>String(text).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;"),
    toast(){}, refreshReviewCount:async()=>{}, refreshExportState:async()=>{},
    window:{confirm:()=>true,openReviewSearch:options=>navigated.push(options)},
    API:{get:async path=>{
        calls.push({kind:"get",path});
        if(path.startsWith("/glossary/dynamic"))return payload;
        const params=new URLSearchParams(path.split("?")[1]);
        return {source:"アリス",target:params.get("target"),target_valid:true,entry_count:2,occurrence_count:3,affected_count:2,offset:0,limit:10,
            items:[{row:40,original:"アリス<script>alert(1)</script>",translated:"<img src=x>",preview_translation:"<img src=x>",effect:"revalidate"}]};
    },post:async(path,body)=>{
        calls.push({kind:"post",path,body});payload={...payload,terms:{"アリス":body.target}};return {updated_cells:2};
    }}, URLSearchParams,
};
vm.createContext(context);
vm.runInContext(fs.readFileSync("ui/static/js/glossary.js","utf8"),context);
(async()=>{
    context.initGlossaryWorkspace();
    context.renderGlossary();
    assert.ok(nodes["glossary-body"].innerHTML.includes("待确认译法"));
    assert.ok(nodes["glossary-body"].innerHTML.includes("出现在说话人位置"));
    assert.ok(!nodes["glossary-body"].innerHTML.includes(">candidate<"));
    await context.openGlossaryDetail("アリス");
    assert.ok(nodes["glossary-detail-summary"].textContent.includes("出现 3 次"));
    assert.ok(nodes["glossary-occurrences"].innerHTML.includes("&lt;script&gt;"));
    assert.ok(!nodes["glossary-occurrences"].innerHTML.includes("<script>"));
    assert.equal(calls.filter(c=>c.kind==="post").length,0,"Preview must never apply terminology");
    nodes["glossary-detail-target"].value="艾莉丝";
    nodes["glossary-detail-target"].events.input.call(nodes["glossary-detail-target"]);
    assert.equal(nodes["btn-glossary-apply"].disabled,true);
    await context.applyGlossaryDetail({preventDefault(){}});
    assert.equal(calls.filter(c=>c.kind==="post").length,0,"Changed targets must require a new preview");
    await context.previewGlossaryImpact(0);
    await context.applyGlossaryDetail({preventDefault(){}});
    const apply=calls.find(c=>c.kind==="post");
    assert.equal(apply.path,"/glossary/promote");
    assert.equal(apply.body.target,"艾莉丝");
    assert.equal(context.glossaryHasUnsavedChanges(),false);
    nodes["btn-glossary-related"].events.click();
    assert.equal(navigated[0].query,"アリス");
    assert.equal(navigated[0].scope,"source");
    assert.equal(navigated[0].filter,"all");
})().catch(error=>{console.error(error);process.exitCode=1;});
''',
    )
    assert result.returncode == 0, result.stdout + result.stderr
