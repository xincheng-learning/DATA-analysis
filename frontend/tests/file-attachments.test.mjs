import assert from 'node:assert/strict';
import {test} from 'node:test';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const filename=fileURLToPath(new URL('../src/components/chat-input.tsx',import.meta.url));
const elements=node=>!node||typeof node!=='object'?[]:[node,...React.Children.toArray(node.props?.children).flatMap(elements)];
function harness(props={}){
  let cursor=0,refCursor=0,effects=[];const states=[],refs=[],events={},revoked=[];
  const react={...React,useCallback:fn=>fn,useState(initial){const i=cursor++;if(!(i in states))states[i]=initial;return [states[i],value=>{states[i]=typeof value==='function'?value(states[i]):value;}];},useRef(initial){const i=refCursor++;return refs[i]??=( {current:initial});},useEffect(fn){effects.push(fn);}};
  const compiled={exports:{}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,{exports:compiled.exports,module:compiled,URL:{createObjectURL:file=>'blob:'+file.name,revokeObjectURL:url=>revoked.push(url)},document:{addEventListener:(name,fn)=>events[name]=fn,removeEventListener(){}},require:name=>name==='react'?react:createRequire(filename)(name)});
  function render(){cursor=0;refCursor=0;effects=[];return elements(compiled.exports.ChatInput({onSend:async()=>true,...props}));}
  return {render,events,revoked,runEffects(){effects.forEach(fn=>fn());}};
}
const csv={name:'sales.csv',size:200,type:'text/csv'},image={name:'sql.png',size:200,type:'image/png'};
test('suggested analysis appends to an existing draft and keeps queued files without sending',()=>{
  let sent=0;const props={onSend:()=>{sent++;},draftRequest:null};const h=harness(props);
  let tree=h.render();tree.find(n=>n.type==='textarea').props.onChange({target:{value:'保留我的业务背景',style:{},scrollHeight:36}});
  tree.find(n=>n.type==='input').props.onChange({target:{files:[csv]}});
  props.draftRequest={text:'比较商品金额和订单。',id:1};h.render();h.runEffects();tree=h.render();
  assert.equal(tree.find(n=>n.type==='textarea').props.value,'保留我的业务背景\n\n比较商品金额和订单。');
  assert.equal(tree.filter(n=>n.props?.['aria-label']==='移除文件 sales.csv').length,1);assert.equal(sent,0);
});
test('picking files queues removable attachments and submits files with the question',async()=>{
  let sent;const h=harness({onSend:async(...args)=>{sent=args;return true;}});
  let tree=h.render();
  tree.find(n=>n.type==='input'&&n.props.type==='file').props.onChange({target:{files:[csv,image]}});
  tree=h.render();assert.equal(tree.filter(n=>n.props?.['aria-label']?.startsWith('移除文件 ')).length,2);
  tree.find(n=>n.type==='textarea').props.onChange({target:{value:'检查 SQL',style:{},scrollHeight:36}});
  tree=h.render();await tree.find(n=>n.props?.['aria-label']==='发送消息').props.onClick();
  assert.equal(sent[0],'检查 SQL');assert.deepEqual(Array.from(sent[1]),[csv,image]);
  tree=h.render();assert.equal(tree.filter(n=>n.props?.['aria-label']?.startsWith('移除文件 ')).length,0);
  assert.deepEqual(h.revoked,['blob:sql.png']);
});
test('failed sending keeps image and draft but removes only imported tables',async()=>{
  const h=harness({onSend:async()=>({sent:false,uploaded:[csv]})});
  let tree=h.render();tree.find(n=>n.type==='input').props.onChange({target:{files:[csv,image]}});
  tree.find(n=>n.type==='textarea').props.onChange({target:{value:'保留草稿',style:{},scrollHeight:36}});
  tree=h.render();await tree.find(n=>n.props?.['aria-label']==='发送消息').props.onClick();
  tree=h.render();assert.equal(tree.find(n=>n.type==='textarea').props.value,'保留草稿');
  assert.equal(tree.filter(n=>n.props?.['aria-label']==='移除文件 sql.png').length,1);
  assert.equal(tree.filter(n=>n.props?.['aria-label']==='移除文件 sales.csv').length,0);
});
test('file drop prevents navigation even when busy; idle drop queues files',()=>{
  for(const loading of [true,false]){
    const h=harness({loading});h.render();h.runEffects();let prevented=0;
    assert.equal(typeof h.events.drop,'function');
    h.events.drop({dataTransfer:{types:['Files'],files:[image]},preventDefault(){prevented++;}});
    assert.equal(prevented,1);
    assert.equal(h.render().filter(n=>n.props?.['aria-label']==='移除文件 sql.png').length,loading?0:1);
  }
});
test('switching chats clears pending files and releases image previews',()=>{
  const props={revision:0},h=harness(props);
  let tree=h.render();h.runEffects();
  tree.find(n=>n.type==='input').props.onChange({target:{files:[image]}});
  props.revision=1;h.render();h.runEffects();
  tree=h.render();assert.equal(tree.filter(n=>n.props?.['aria-label']==='移除文件 sql.png').length,0);
  assert.deepEqual(h.revoked,['blob:sql.png']);
});
test('unsupported, oversized and excessive files never enter the pending queue',()=>{
  for(const files of [[{name:'script.svg',size:100}],[{name:'large.png',size:10*1024*1024+1}],Array.from({length:6},()=>csv)]){
    const h=harness();let tree=h.render();tree.find(n=>n.type==='input').props.onChange({target:{files}});
    tree=h.render();assert.equal(tree.filter(n=>n.props?.['aria-label']?.startsWith('移除文件 ')).length,0);
    assert.equal(tree.filter(n=>n.props?.role==='alert').length,1);
  }
});
test('pasted screenshots join the same queue while text paste stays native',()=>{
  const h=harness();let tree=h.render();let prevented=0;
  const input=tree.find(n=>n.type==='textarea');
  assert.equal(typeof input.props.onPaste,'function');
  input.props.onPaste({clipboardData:{items:[{kind:'string',type:'text/plain'}]},preventDefault(){prevented++;}});
  assert.equal(prevented,0);
  input.props.onPaste({clipboardData:{items:[{kind:'file',type:'image/png',getAsFile:()=>image}]},preventDefault(){prevented++;}});
  assert.equal(prevented,1);tree=h.render();
  assert.equal(tree.filter(n=>n.props?.['aria-label']==='移除文件 sql.png').length,1);
});
