import assert from 'node:assert/strict';
import {test} from 'node:test';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';

const elements=node=>!node||typeof node!=='object'?[]:[node,...React.Children.toArray(node.props?.children).flatMap(elements)];
function load(relative,overrides,globals={}){
  const filename=fileURLToPath(new URL(`../src/${relative}`,import.meta.url)),compiled={exports:{}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,{exports:compiled.exports,module:compiled,require:name=>overrides[name]||createRequire(filename)(name),...globals});
  return compiled.exports;
}

test('a whole conversation is selected without message checkboxes or loading the transcript',()=>{
  let picked,reads=0;const state=[[{session_id:'source',title:'销售复盘'}],'source','',[],[],'',false];
  const {ContextPicker}=load('components/context-picker.tsx',{react:{...React,useState:()=>[state.shift(),()=>{}],useEffect(){}},'@base-ui/react/dialog':{Dialog:{}},'@/lib/api':{getSession:()=>reads++},'@/lib/chart':{},'@/lib/analysis':{}});
  const tree=elements(ContextPicker({mode:'conversation',currentSessionId:'current',onPick:value=>picked=value,onClose(){}}));
  assert.equal(tree.filter(node=>node.props?.type==='checkbox').length,0);
  tree.find(node=>node.type==='button'&&node.props.children==='添加到问题').props.onClick();
  assert.equal(picked.session_id,'source');assert.equal(picked.message_ids,undefined);assert.equal(reads,0);
});

test('a conversation request only sends the source ID, never a draft transcript',async()=>{
  let body;
  const api=load('lib/api.ts',{'./supabase':{supabase:null}},{process:{env:{}},fetch:async(_,options)=>{body=JSON.parse(options.body);return {ok:true,json:async()=>({})};}});
  await api.startAnalysis('比较结果','current','quick',{conversation:{session_id:'source',title:'销售复盘',message_ids:[1,2],messages:['不应发送的原文']}});
  assert.deepEqual(body.conversation,{session_id:'source'});
});

test('the context ring reports memory use separately from billing and official model capacity',()=>{
  const state=[{key:'test:2:false',status:{used_tokens:4000,limit_tokens:16000,trigger_tokens:12800,compressed:true,summarized_messages:20},failed:false}];
  const {ContextMeter}=load('components/context-meter.tsx',{react:{...React,useState:()=>[state.shift(),()=>{}],useEffect(){}},'@/lib/api':{}});
  const tree=elements(ContextMeter({sessionId:'test',revision:2,pending:false}));
  assert.equal(tree.find(node=>node.type==='summary').props['aria-label'],'上下文已用 25%');
  const text=tree.flatMap(node=>React.Children.toArray(node.props?.children).filter(child=>typeof child==='string')).join('');
  assert.match(text,/80%/);assert.match(text,/非累计计费 Token 或模型官方上限/);assert.match(text,/较早 20 条/);
});

test('a different conversation cannot display the previous conversation memory status',()=>{
  const {ContextMeter}=load('components/context-meter.tsx',{react:{...React,useState:()=>[{key:'old:2:false',status:{used_tokens:4000,limit_tokens:16000},failed:false},()=>{}],useEffect(){}},'@/lib/api':{}});
  const tree=elements(ContextMeter({sessionId:'new',revision:2,pending:false}));
  assert.equal(tree.find(node=>node.type==='summary').props['aria-label'],'读取上下文…');
});
