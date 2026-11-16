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
function load(react){const compiled={exports:{}};vm.runInNewContext(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,{exports:compiled.exports,module:compiled,require:name=>name==='react'?react:createRequire(filename)(name)});return compiled.exports.ChatInput;}

test('the plus menu integrates upload, chat context, charts and panels without clearing a draft',()=>{
  let fileOpened=0,chat=0,chart=0,panel=0;
  const refs=[{current:{click:()=>fileOpened++}},{current:null},{current:null}];
  const ChatInput=load({...React,useCallback:fn=>fn,useState:initial=>[typeof initial==='string'?'草稿':typeof initial==='boolean'?true:initial,()=>{}],useRef:()=>refs.shift()||{current:null},useEffect(){}});
  const tree=elements(ChatInput({onSend(){},onUpload(){},onPickConversation:()=>chat++,onPickCharts:()=>chart++,onPickPanel:()=>panel++,hasCharts:true}));
  const items=tree.filter(node=>node.props?.role==='menuitem');assert.equal(items.length,4);
  items.forEach(item=>item.props.onClick());
  assert.deepEqual([fileOpened,chat,chart,panel],[1,1,1,1]);
  assert.equal(tree.find(node=>node.type==='textarea').props.value,'草稿');
});

test('an open plus menu cannot expose actions while history or analysis is busy',()=>{
  for(const busy of [{loading:true},{disabled:true}]){
    const ChatInput=load({...React,useCallback:fn=>fn,useState:initial=>[typeof initial==='string'?'草稿':typeof initial==='boolean'?true:initial,()=>{}],useRef:()=>({current:null}),useEffect(){}});
    const tree=elements(ChatInput({...busy,onSend(){},onUpload(){},onPickPanel(){}}));
    assert.equal(tree.filter(node=>node.props?.role==='menuitem').length,0);
    assert.equal(tree.find(node=>node.props?.['aria-label']==='添加附件').props.disabled,true);
  }
});

test('a delayed acceptance never clears a newly typed next question',async()=>{
  let value='原问题',resolve;
  let first=true;
  const ChatInput=load({...React,useCallback:fn=>fn,useState:initial=>first?(first=false,[value,update=>{value=typeof update==='function'?update(value):update;}]):[initial,()=>{}],useRef:()=>({current:null}),useEffect(){}});
  const tree=elements(ChatInput({onSend:()=>new Promise(done=>resolve=done),onUpload(){}}));
  const pending=tree.find(node=>node.props?.['aria-label']==='发送消息').props.onClick();
  value='下一轮的新问题';resolve(true);await pending;
  assert.equal(value,'下一轮的新问题');
});
