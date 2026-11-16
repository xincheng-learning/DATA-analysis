import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import vm from "node:vm";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

function load(relative, overrides = {}, globals = {}) {
  const filename = fileURLToPath(new URL(`../src/${relative}`, import.meta.url));
  const compiled = { exports: {} };
  const require = createRequire(filename);
  const { outputText } = ts.transpileModule(fs.readFileSync(filename, "utf8"), {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, esModuleInterop: true },
  });
  vm.runInNewContext(outputText, {
    exports: compiled.exports, module: compiled, process: { env: {} }, URL,
    require: (name) => overrides[name] || (name==='@/lib/analysis'?load('lib/analysis.ts'):require(name)), ...globals,
  });
  return compiled.exports;
}

function elements(element) {
  if (!element || typeof element !== "object") return [];
  const children = React.Children.toArray(element.props?.children);
  return [element, ...children.flatMap(elements)];
}

test("busy input and Chinese IME Enter cannot accidentally start a second analysis", () => {
  let sends = 0;
  const { ChatInput } = load("components/chat-input.tsx", {
    react: { ...React,useCallback:fn=>fn, useState: initial => [initial===''?'销售额':initial, () => {}], useRef: initial => ({ current: initial }),useEffect(){} },
  });
  const props = { onSend: () => { sends++; }, onUpload() {} };
  for (const [loading, isComposing, disabled] of [[true, false, false], [false, true, false], [false, false, true]]) {
    const textarea = elements(ChatInput({ ...props, loading, disabled })).find((node) => node.type === "textarea");
    textarea.props.onKeyDown({ key: "Enter", shiftKey: false, nativeEvent: { isComposing }, preventDefault() {} });
  }
  assert.equal(sends, 0);
});

test("task errors retain HTTP status so a missing task differs from a temporary disconnect", async () => {
  const api = load("lib/api.ts", { "./supabase": { supabase: null } }, {
    fetch: async () => ({ ok: false, status: 404, json: async () => ({ detail: "任务记录不存在" }) }),
  });
  await assert.rejects(api.readAnalysis("missing"), (error) => error.status === 404 && error.message === "任务记录不存在");
});

test('failed submission keeps the typed question while successful acceptance clears it',async()=>{
  for(const accepted of [false,true]){
    const updates=[];
    const {ChatInput}=load('components/chat-input.tsx',{
      react:{...React,useCallback:fn=>fn,useState:initial=>[initial===''?'保留这个问题':initial,value=>{if(typeof initial==='string')updates.push(typeof value==='function'?value('保留这个问题'):value);}],useRef:initial=>({current:initial}),useEffect(){}},
    });
    const tree=elements(ChatInput({onSend:async()=>accepted,onUpload(){},quote:{message_id:18,text:'引用片段'}}));
    await tree.find(node=>node.props?.['aria-label']==='发送消息').props.onClick();
    assert.equal(updates.includes(''),accepted);
  }
});

test('panel and quote are structured request attachments rather than SQL or question text',async()=>{
  let sent;
  const api=load('lib/api.ts',{'./supabase':{supabase:null}},{fetch:async(_url,options)=>{sent=JSON.parse(options.body);return{ok:true,json:async()=>({task_id:'test'})};}});
  await api.startAnalysis('说明原因','session','quick',{quote:{message_id:18,text:'峰值'},board:{id:'a'.repeat(32),version:7}});
  assert.equal(sent.question,'说明原因');assert.equal(sent.quote.message_id,18);assert.equal(sent.board.version,7);assert.equal(sent.sql,undefined);
});

test("evidence renders saved SQL, failures, result values and investigation gaps", () => {
  const data = load("components/data-panel.tsx", { "@/lib/api": {} });
  const { AnalysisEvidence } = load("components/analysis-evidence.tsx", { "@/components/data-panel": data });
  const html = renderToStaticMarkup(React.createElement(AnalysisEvidence, { metadata: {
    sql: "SELECT actual FROM sales", rows: [{ actual: 900 }], columns: ["actual"], row_count: 1,
    previous_attempts: [{ sql: "SELECT missing FROM sales", error: "列不存在" }],
    investigation: { status: "budget_exhausted", reason: "仍缺区域证据", steps: [] },
    evidence: { model_calls: 2, max_model_calls: 2, trace: [{ label: "执行查询", seconds: 0.01 }] },
  } }));
  for (const expected of ["SELECT actual", "900", "列不存在", "仍缺区域证据", "预算已用尽", "执行查询"]) assert.ok(html.includes(expected), expected);
});

test('Python evidence shows actual code and tool output, including a failed run',()=>{
  const {AnalysisEvidence}=load('components/analysis-evidence.tsx',{'@/components/data-panel':{ResultTable:()=>null}});
  const metadata={route:'python',python_code:'result = 150.0',python_output:'150.0'};
  const html=renderToStaticMarkup(React.createElement(AnalysisEvidence,{metadata}));
  assert.ok(html.includes('result = 150.0')&&html.includes('150.0')&&html.includes('Python 代码与工具输出'));
  const failed=renderToStaticMarkup(React.createElement(AnalysisEvidence,{metadata:{...metadata,python_output:'',error:'Docker 不可用'}}));
  assert.ok(failed.includes('Docker 不可用'));
});

test("task cancellation reaches the backend rather than aborting only the page request", async () => {
  const calls = [];
  const api = load("lib/api.ts", { "./supabase": { supabase: null } }, {
    fetch: async (url, options) => { calls.push({ url, options }); return { ok: true, json: async () => ({ status: "cancelling" }) }; },
  });
  await api.cancelAnalysis("task-one");
  assert.ok(calls[0].url.endsWith("/tasks/task-one/cancel"));
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.cache, "no-store");
});

test("unconfigured model opens settings without erasing the typed question", () => {
  const writes = [];
  let opened = false;
  let sent = false;
  const { ChatInput } = load("components/chat-input.tsx", {
    react: { ...React,useCallback:fn=>fn, useState: initial => [initial===''?'销售额为什么下降？':initial, (value) => writes.push(value)], useRef: initial => ({ current: initial }),useEffect(){} },
  });
  const tree = ChatInput({ onConfigureModel: () => { opened = true; }, onSend: () => { sent = true; }, onUpload() {} });
  const send = elements(tree).find((node) => node.props["aria-label"] === "配置模型后发送");
  send.props.onClick();
  assert.equal(opened, true);
  assert.equal(sent, false);
  assert.deepEqual(writes, []);
});

test("closing settings clears the password and cancels a pending connection test", () => {
  const key = { current: { value: "test-only-secret" } };
  let aborted = false;
  let closed = false;
  const refs = [key, { current: { abort: () => { aborted = true; } } }];
  const { ModelSettings } = load("components/model-settings.tsx", {
    react: { ...React,useCallback:fn=>fn, useState: (initial) => [initial, () => {}], useRef: () => refs.shift(), useEffect() {} },
    "@/components/ui/button": { Button: "button" }, "@/components/ui/input": { Input: "input" },
    "@/lib/api": {},
  });
  const tree = ModelSettings({ open: true, onOpenChange: (open) => { closed = !open; }, onSaved() {} });
  tree.props.onOpenChange(false);
  assert.equal(key.current.value, "");
  assert.equal(aborted, true);
  assert.equal(closed, true);
});

test("settings requests avoid browser caching and send credentials only on an explicit action", async () => {
  const calls = [];
  const api = load("lib/api.ts", { "./supabase": { supabase: null } }, {
    fetch: async (url, options) => {
      calls.push({ url, options });
      return { ok: true, json: async () => ({ configured: true, has_api_key: true, message: "通过" }) };
    },
  });
  assert.equal(calls.length, 0);
  await api.getModelSettings();
  assert.equal(calls[0].options.cache, "no-store");
  await api.saveModelSettings({ base_url: "https://example.invalid/v1", model: "offline-model", api_key: "test-only-secret" });
  assert.equal(calls[1].options.method, "PUT");
  assert.equal(JSON.parse(calls[1].options.body).api_key, "test-only-secret");
  assert.equal(calls.length, 2);
});

test("saving clears the password and publishes the non-secret status", async () => {
  const status = { provider: "openai_compatible", base_url: "https://example.invalid/v1", has_api_key: false };
  const states = [status, status.base_url, "offline-model", false, null, "", ""];
  const key = { current: { value: "test-only-secret" } };
  const refs = [key, { current: null }];
  let submitted;
  let published;
  const { ModelSettings } = load("components/model-settings.tsx", {
    react: { ...React,useCallback:fn=>fn, useState: () => [states.shift(), () => {}], useRef: () => refs.shift(), useEffect() {} },
    "@/components/ui/button": { Button: "button" }, "@/components/ui/input": { Input: "input" },
    "@/lib/api": { saveModelSettings: async (input) => { submitted = input; return { configured: true }; } },
  });
  const tree = ModelSettings({ open: true, onOpenChange() {}, onSaved: (value) => { published = value; } });
  elements(tree).find((node) => node.type === "form").props.onSubmit({ preventDefault() {} });
  await new Promise(setImmediate);
  assert.equal(submitted.api_key, "test-only-secret");
  assert.equal(key.current.value, "");
  assert.deepEqual(published, { configured: true });
});

test("thinking mode controls are shown for DeepSeek and both actions send the chosen settings", async () => {
  const requests = [];
  for (const action of ["save", "test"]) {
    const status = { provider: "openai_compatible", base_url: "https://api.deepseek.com", has_api_key: true };
    const states = [status, status.base_url, "deepseek-flash", false, null, "", "", "enabled", "low"];
    const refs = [{ current: { value: "" } }, { current: null }];
    const { ModelSettings } = load("components/model-settings.tsx", {
      react: { ...React,useCallback:fn=>fn, useState: (initial) => [states.length ? states.shift() : initial, () => {}], useRef: () => refs.shift(), useEffect() {} },
      "@/components/ui/button": { Button: "button" }, "@/components/ui/input": { Input: "input" },
      "@/lib/api": {
        saveModelSettings: async (config) => { requests.push(config); return { configured: true }; },
        testModelSettings: async (config) => { requests.push(config); return "通过"; },
      },
    }, { AbortController });
    const tree = ModelSettings({ open: true, onOpenChange() {}, onSaved() {} });
    const nodes = elements(tree);
    assert.ok(nodes.some((node) => node.props.id === "model-thinking-mode"));
    assert.ok(nodes.some((node) => node.props.id === "model-reasoning-effort"));
    if (action === "save") nodes.find((node) => node.type === "form").props.onSubmit({ preventDefault() {} });
    else nodes.find((node) => node.props.children === "测试连接").props.onClick();
    await new Promise(setImmediate);
  }
  assert.equal(requests.length, 2);
  for (const config of requests) {
    assert.equal(config.thinking_mode, "enabled");
    assert.equal(config.reasoning_effort, "low");
    assert.equal(config.api_key, undefined);
  }
});

test("unverified provider endpoints cannot display misleading thinking controls", () => {
  const states = [null, "https://api.deepseek.com.example.invalid", "model", false, null, "", "", "auto", "low"];
  const { ModelSettings } = load("components/model-settings.tsx", {
    react: { ...React,useCallback:fn=>fn, useState: () => [states.shift(), () => {}], useRef: () => ({ current: null }), useEffect() {} },
    "@/components/ui/button": { Button: "button" }, "@/components/ui/input": { Input: "input" }, "@/lib/api": {},
  });
  const tree = ModelSettings({ open: true, onOpenChange() {}, onSaved() {} });
  assert.ok(!elements(tree).some((node) => node.props.id === "model-thinking-mode"));
});

test("reopening settings restores the saved thinking preference", async () => {
  const writes = [];
  const { ModelSettings } = load("components/model-settings.tsx", {
    react: { ...React,useCallback:fn=>fn, useState: (initial) => [initial, (value) => writes.push(value)], useRef: () => ({ current: null }), useEffect: (effect) => { effect(); } },
    "@/components/ui/button": { Button: "button" }, "@/components/ui/input": { Input: "input" },
    "@/lib/api": { getModelSettings: async () => ({ provider: "openai_compatible", base_url: "https://api.deepseek.com", model: "deepseek-flash", thinking_mode: "enabled", reasoning_effort: "high" }) },
  });
  ModelSettings({ open: true, onOpenChange() {}, onSaved() {} });
  await new Promise(setImmediate);
  assert.ok(writes.includes("enabled") && writes.includes("high"));
});


test('toolbar thinking saves supported strength without copying a secret and keeps status on failure',async()=>{
  const status={configured:true,thinking_supported:true,base_url:'https://api.deepseek.com',model:'deepseek-flash',thinking_mode:'auto',reasoning_effort:'low'};
  for(const fail of [false,true]){
    let sent,published;const busy=[];
    const {ThinkingControl}=load('components/thinking-control.tsx',{
      react:{...React,useCallback:fn=>fn,useState:initial=>[initial,()=>{}]},
      '@/lib/api':{saveModelSettings:async config=>{sent=config;if(fail)throw new Error('save failed');return {...status,thinking_mode:'enabled',reasoning_effort:'high'};}},
    });
    const select=elements(ThinkingControl({status,disabled:false,onSaved:value=>published=value,onBusy:value=>busy.push(value)})).find(node=>node.type==='select');
    select.props.onChange({target:{value:'high'}});
    await new Promise(setImmediate);
    assert.equal(sent.reasoning_effort,'high');assert.equal(sent.thinking_mode,'enabled');assert.equal(sent.api_key,undefined);
    assert.equal(Boolean(published),!fail);assert.deepEqual(busy,[true,false]);
  }
});

test('unsupported model and active task disable toolbar thinking changes',()=>{
  const {ThinkingControl}=load('components/thinking-control.tsx',{'@/lib/api':{},react:{...React,useCallback:fn=>fn,useState:initial=>[initial,()=>{}]}});
  for(const [supported,disabled] of [[false,false],[true,true]]){
    const select=elements(ThinkingControl({status:{configured:true,thinking_supported:supported,thinking_mode:'auto'},disabled,onSaved(){},onBusy(){}})).find(node=>node.type==='select');
    assert.equal(select.props.disabled,true);
  }
});
