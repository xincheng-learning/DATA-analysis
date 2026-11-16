import assert from 'node:assert/strict';
import {test} from 'node:test';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const filename=fileURLToPath(new URL('../src/components/clarification-card.tsx',import.meta.url));
const elements=node=>!node||typeof node!=='object'?[]:[node,...React.Children.toArray(node.props?.children).flatMap(elements)];
test('a question requires an explicit answer and allows a choice plus free text',async()=>{
  let cursor=0,sent;const states=[];
  const compiled={exports:{}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,{exports:compiled.exports,module:compiled,require:name=>name==='react'?{...React,useId:()=> 'question',useState(initial){const i=cursor++;states[i]??=initial;return[states[i],value=>states[i]=value];}}:createRequire(filename)(name)});
  const render=()=>{cursor=0;return elements(compiled.exports.ClarificationCard({question:{question:'使用哪种金额？',why:'决定计算字段',options:['商品金额','支付金额'],answers:[],goal:'分析销售'},onAnswer:async answer=>{sent=answer;return true;}}));};
  let tree=render();assert.equal(tree.find(n=>n.type==='button'&&n.props.type==='submit').props.disabled,true);
  tree.find(n=>n.type==='input'&&n.props.type==='radio').props.onChange();
  tree.find(n=>n.type==='textarea').props.onChange({target:{value:'只统计已送达订单'}});
  tree=render();await tree.find(n=>n.type==='form').props.onSubmit({preventDefault(){}});
  assert.ok(sent.includes('商品金额')&&sent.includes('只统计已送达订单'));
});

test('grouped decisions show suggestions without selecting them and require every answer',async()=>{
  let cursor=0,sent;const states=[],compiled={exports:{}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,{exports:compiled.exports,module:compiled,require:name=>name==='react'?{...React,useId:()=> 'grouped',useState(initial){const i=cursor++;states[i]??=initial;return[states[i],value=>states[i]=typeof value==='function'?value(states[i]):value];}}:createRequire(filename)(name)});
  const question={question:'请确认业务口径',options:[],goal:'分析销售',answers:[],decisions:[
    {id:'R1',question:'销售额是否含运费？',why:'金额定义不同',options:['不含运费','含运费'],recommendation:'不含运费',impact:'影响销售额'},
    {id:'R2',question:'比较基准？',why:'比较窗口会影响变化率',options:['上月','去年同月'],recommendation:'',impact:'影响增长率'}]};
  const render=()=>{cursor=0;return elements(compiled.exports.ClarificationCard({question,onAnswer:async(answer,answers)=>{sent={answer,answers};return true;}}));};
  let tree=render();assert.equal(tree.find(n=>n.type==='button'&&n.props.type==='submit').props.disabled,true);
  assert.ok(tree.filter(n=>n.type==='input').every(n=>!n.props.checked));
  assert.ok(tree.some(n=>String(n.props?.children).includes('不含运费')));
  tree.find(n=>n.type==='input'&&n.props.value==='不含运费').props.onChange();
  tree=render();assert.equal(tree.find(n=>n.type==='button'&&n.props.type==='submit').props.disabled,true);
  tree.find(n=>n.type==='input'&&n.props.value==='上月').props.onChange();
  tree.find(n=>n.type==='textarea'&&n.props['aria-label'].includes('R2')).props.onChange({target:{value:'改为去年同月'}});
  tree=render();assert.equal(tree.find(n=>n.type==='button'&&n.props.type==='submit').props.disabled,false);
  await tree.find(n=>n.type==='form').props.onSubmit({preventDefault(){}});
  assert.equal(sent.answers.R1,'不含运费');assert.equal(sent.answers.R2,'改为去年同月');
  assert.ok(sent.answer.includes('比较基准？')&&sent.answer.includes('改为去年同月'));
});
