import assert from 'node:assert/strict';
import {test} from 'node:test';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const filename=fileURLToPath(new URL('../src/app/page.tsx',import.meta.url));
const elements=node=>!node||typeof node!=='object'?[]:[node,...React.Children.toArray(node.props?.children).flatMap(elements)];
const csv={name:'sales.csv'},image={name:'sql.png'};
test('analysis starters populate an editable draft without submitting a task',()=>{
  let calls=0;const h=page({startAnalysis:async()=>{calls++;throw Error('not a submit action');}});
  const starter=h.render().find(n=>typeof n.props?.onChoose==='function');
  starter.props.onChoose('先检查数据，不执行清洗。');
  assert.equal(h.input().draftRequest.text,'先检查数据，不执行清洗。');
  starter.props.onChoose('比较订单金额。');
  assert.equal(h.input().draftRequest.text,'比较订单金额。');
  assert.equal(h.input().draftRequest.id,2);assert.equal(calls,0);
});

test('latest message actions retain the original goal and confirm only their bound cleaning plan',async()=>{
  let calls=0,submitted;
  const h=page({getSession:async()=>({messages:[
    {id:1,role:'user',content:'清洗后统计金额',metadata:{}},
    {id:2,role:'assistant',content:'旧结果',metadata:{rows:[{amount:10}],columns:['amount']}},
    {id:3,role:'assistant',content:'等待确认',metadata:{workflow:{status:'waiting',goal:'清洗后统计金额'}}},
  ]}),startAnalysis:async(...args)=>{calls++;submitted=args;return{task_id:'t',session_id:'s'};}});
  await h.sidebar().onSelectSession('s');
  let replies=h.render().filter(n=>n.props?.role==='assistant');
  assert.equal(replies[0].props.onFollowUp,undefined);assert.equal(replies[0].props.onConfirmCleaning,undefined);
  assert.equal(replies[1].props.originalRequest,'清洗后统计金额');
  replies[1].props.onFollowUp('保留原条件，改成六月');
  assert.equal(h.input().draftRequest.text,'保留原条件，改成六月');assert.equal(calls,0);
  assert.equal(await replies[1].props.onConfirmCleaning('a'.repeat(32)),true);
  assert.equal(calls,1);assert.equal(submitted[1],'s');
  assert.equal(submitted[3].cleaning_reply.message_id,3);assert.equal(submitted[3].cleaning_reply.plan_id,'a'.repeat(32));
  assert.equal(Object.keys(submitted[3]).join(','),'cleaning_reply');
  assert.equal(h.input().draftRequest.text,'保留原条件，改成六月');
});
function page(api){
  let cursor=0;const states=[],refs=[];let refCursor=0;
  function ChatInput(){} function Sidebar(){}
  const mocks={'@/lib/analysis':new Proxy({},{get:(_,name)=>name==='withScopeHistory'?(messages=>messages):name==='reportSources'?()=>[]:()=>null}),'@/components/chat-input':{ChatInput},'@/components/sidebar':{Sidebar},'@/components/auth-provider':{useAuth:()=>({user:null,loading:false})},'@/lib/supabase':{isLocalMode:true},'@/lib/api':{listSessions:async()=>[],getSessionTables:async()=>[],...api}};
  const compiled={exports:{}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,{exports:compiled.exports,module:compiled,AbortController,crypto:{randomUUID:()=> 'same-chat'},sessionStorage:{removeItem(){},setItem(){},getItem(){return null;}},require:name=>name==='react'?{...React,useSyncExternalStore:()=>null,useState(initial){const i=cursor++;if(!(i in states))states[i]=initial;return[states[i],update=>{states[i]=typeof update==='function'?update(states[i]):update;}];},useEffect(){},useRef(initial){return refs[refCursor++]??={current:initial};}}:mocks[name]||(name.startsWith('@/')?new Proxy({},{get:()=>()=>null}):createRequire(filename)(name))});
  function render(){cursor=0;refCursor=0;return elements(compiled.exports.default());}
  return{input:()=>render().find(n=>n.type===ChatInput).props,sidebar:()=>render().find(n=>n.type===Sidebar).props,render};
}
test('a first mixed upload and task share one session; images are structured attachments',async()=>{
  let uploadSid,taskSid,context;
  const h=page({uploadFiles:async sid=>{uploadSid=sid;return{uploaded:[{filename:'sales.csv',upload_index:0},{filename:'sql.png',upload_index:1,image:{id:'a'.repeat(64)+'.png',filename:'sql.png'}}],errors:[]};},startAnalysis:async(question,sid,mode,attachments)=>{taskSid=sid;context=attachments;return{task_id:'task',session_id:sid};}});
  assert.equal(await h.input().onSend('检查 SQL',[csv,image]),true);
  assert.equal(uploadSid,'samechat');assert.equal(taskSid,uploadSid);
  assert.equal(context.images[0].filename,'sql.png');
});
test('partial upload stops before model and reports only imported File identities',async()=>{
  let calls=0;
  const h=page({uploadFiles:async()=>({uploaded:[{filename:'sales.csv',upload_index:0}],errors:[{filename:'sql.png',upload_index:1,error:'损坏'}]}),startAnalysis:async()=>{calls++;}});
  const result=await h.input().onSend('检查 SQL',[csv,image]);
  assert.equal(result.sent,false);assert.deepEqual(Array.from(result.uploaded),[csv]);assert.equal(calls,0);
});
test('stopping while upload is pending prevents the later billable model request',async()=>{
  let release,calls=0;
  const h=page({uploadFiles:()=>new Promise(resolve=>release=resolve),startAnalysis:async()=>{calls++;}});
  const pending=h.input().onSend('检查 SQL',[image]);
  await h.input().onStop();
  release({uploaded:[{filename:'sql.png',upload_index:0,image:{id:'a'.repeat(64)+'.png',filename:'sql.png'}}],errors:[]});
  const result=await pending;assert.equal(result.sent,false);assert.equal(calls,0);
});
test('a file-only table send imports without calling a model',async()=>{
  let calls=0;
  const h=page({uploadFiles:async()=>({uploaded:[{filename:'sales.csv',upload_index:0}],errors:[]}),startAnalysis:async()=>{calls++;}});
  assert.equal((await h.input().onSend('',[csv])).sent,true);assert.equal(calls,0);
});
test('a successful preview refresh clears an earlier preview failure',async()=>{
  let calls=0;
  const h=page({uploadFiles:async()=>({uploaded:[{filename:'sales.csv',upload_index:0}],errors:[]}),getSessionTables:async()=>{if(++calls===1)throw new Error('temporary');return [];}});
  await h.input().onSend('',[csv]);
  assert.ok(h.render().some(n=>n.props?.error?.includes('表概览读取失败')));
  await h.input().onSend('',[csv]);
  assert.ok(!h.render().some(n=>n.props?.error?.includes('表概览读取失败')));
});

test('cleaning entry stays visible with import guidance and becomes usable after import',async()=>{
  const h=page({uploadFiles:async()=>({uploaded:[{filename:'sales.csv',upload_index:0}],errors:[]}),
    getSessionTables:async()=>[{table_name:'sales',columns:['amount'],rows:1}]});
  const entry=()=>h.render().find(n=>n.props?.['aria-label']==='数据工作台 · 体检与清洗');
  assert.ok(entry(),'the cleaning entry must remain discoverable in an empty chat');
  assert.equal(entry().props.disabled,true);
  assert.match(entry().props.title,/导入/);
  await h.input().onSend('',[csv]);
  assert.equal(entry().props.disabled,false);
});
