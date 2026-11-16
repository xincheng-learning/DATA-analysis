import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import {test} from 'node:test';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const filename=fileURLToPath(new URL('../src/components/sidebar.tsx',import.meta.url));
const elements=node=>!node||typeof node!=='object'?[]:[node,...React.Children.toArray(node.props?.children).flatMap(elements)];
const content=node=>node==null?'':typeof node==='string'||typeof node==='number'?String(node):React.Children.toArray(node.props?.children).map(content).join('');
function harness(props,localRequest=async()=>[]){
  let cursor=0;const states=[];const compiled={exports:{}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2017,esModuleInterop:true}}).outputText,{
    module:compiled,exports:compiled.exports,Error,require:name=>name==='react'?{...React,useEffect(){},useRef:()=>({current:null}),useState:initial=>{const i=cursor++;if(!(i in states))states[i]=initial;return[states[i],value=>states[i]=typeof value==='function'?value(states[i]):value];}}:name==='@base-ui/react/dialog'?{Dialog:new Proxy({},{get:(_,name)=>'Dialog.'+name})}:name.startsWith('@/')?{localRequest,ThemeToggle:()=>null}:createRequire(filename)(name)
  });
  const render=()=>{cursor=0;return compiled.exports.Sidebar(props);};
  return{render,find:fn=>elements(render()).find(fn),text:()=>content(render())};
}
test('batch deletion requires an exact confirmation and retains failed selections',async()=>{
  const deleted=[];const h=harness({sessions:[{session_id:'a',title:'成功聊天'},{session_id:'b',title:'占用聊天'}],activeSessionId:null,
    onDeleteSession:async id=>{if(id==='b')throw new Error('文件被占用，记录已保留');deleted.push(id);},onNewChat(){},onSelectSession(){}});
  h.find(n=>n.type==='button'&&content(n)==='管理').props.onClick();
  h.find(n=>n.props?.['aria-label']==='选择当前列表全部聊天').props.onChange({target:{checked:true}});
  h.find(n=>n.type==='button'&&content(n)==='删除').props.onClick();
  assert.deepEqual(deleted,[]);assert.match(h.text(),/无法撤销/);assert.match(h.text(),/成功聊天/);
  await h.find(n=>n.type==='button'&&content(n)==='确认删除 2 场').props.onClick();
  await new Promise(setImmediate);
  assert.deepEqual(deleted,['a']);assert.match(h.text(),/已删除 1 场聊天，1 场未删除/);
  assert.match(h.text(),/记录已保留/);
  assert.equal(h.find(n=>n.props?.['aria-label']==='选择聊天 占用聊天').props.checked,true);
  assert.equal(h.find(n=>n.props?.['aria-label']==='选择聊天 成功聊天').props.checked,false);
});
test('select all applies only to the searched list',()=>{
  const h=harness({sessions:[{session_id:'a',title:'电商五月'},{session_id:'b',title:'电商六月'},{session_id:'c',title:'库存'}],activeSessionId:null,onDeleteSession:async()=>{},onNewChat(){},onSelectSession(){}});
  h.find(n=>n.props?.['aria-label']==='搜索聊天').props.onChange({target:{value:'电商'}});
  h.find(n=>n.type==='button'&&content(n)==='管理').props.onClick();
  h.find(n=>n.props?.['aria-label']==='选择当前列表全部聊天').props.onChange({target:{checked:true}});
  assert.match(h.text(),/已选 2/);assert.equal(h.find(n=>n.props?.['aria-label']==='选择聊天 库存'),undefined);
});

test('a saved folder cannot be created twice when the following list refresh fails',async()=>{
  let posts=0;const h=harness({sessions:[],activeSessionId:null,onDeleteSession:async()=>{},onStorageSettings(){},onNewChat(){},onSelectSession(){}},async(path,method)=>{
    if(method==='POST'){posts++;return{id:'saved',title:'已保存文件夹'};}throw new Error('列表暂时不可读取');
  });
  h.find(n=>n.props?.['aria-label']==='新建聊天文件夹').props.onClick();
  h.find(n=>n.props?.['aria-label']==='文件夹名称').props.onChange({target:{value:'已保存文件夹'}});
  await h.find(n=>n.type==='form').props.onSubmit({preventDefault(){}});await new Promise(setImmediate);
  assert.equal(posts,1);assert.equal(h.find(n=>n.props?.['aria-label']==='文件夹名称'),undefined);
  assert.match(h.text(),/已保存文件夹/);assert.match(h.text(),/列表暂时不可读取/);
});
