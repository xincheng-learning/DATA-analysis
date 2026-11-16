import assert from 'node:assert/strict';
import fs from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';

const output = ts.transpileModule(fs.readFileSync(new URL('../src/lib/workbench.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText;
const compiledWorkbench = { exports: {} };
vm.runInNewContext(output, { module:compiledWorkbench, exports: compiledWorkbench.exports });
const { makeCleaningRule, metricChanges } = compiledWorkbench.exports;

test('cleaning null changes expose lost values without treating zero as missing',()=>{
  const result=compiledWorkbench.exports.cleaningNullChanges({before_null_counts:{amount:0,region:3,stable:0},after_null_counts:{amount:2,region:0,stable:0}});
  assert.equal(result.length,2);assert.equal(result[0]['变化'],2);assert.equal(result[1]['变化'],-3);
});

test('numeric fills require a real finite number and explicit confirmation', () => {
  assert.throws(() => makeCleaningRule('fill', 'amount', '', '%Y/%m/%d', [], 'first', true, true), /数值/);
  assert.throws(() => makeCleaningRule('fill', 'amount', 'Infinity', '', [], 'first', true, true), /数值/);
  assert.throws(() => makeCleaningRule('fill', 'amount', '0', '', [], 'first', false, true), /确认/);
  assert.equal(makeCleaningRule('fill', 'amount', '0', '', [], 'first', true, true).value, 0);
  assert.equal(makeCleaningRule('fill', 'region', '未知', '', [], 'first', true, false).value, '未知');
});
test('metric changes keep unknown different from zero and preserve currency', () => {
  const result = metricChanges([{ id: 'amount', value: null, label: '金额', unit: 'BRL' }], [{ id: 'amount', value: '0', label: '金额', unit: 'BRL' }]);
  assert.equal(result.length, 1);
  assert.equal(result[0].before, null);
  assert.equal(result[0].after, '0');
  assert.equal(result[0].unit, 'BRL');
});

test('number conversion failures are described as conversion failures in the cleaning preview', () => {
  const filename=fileURLToPath(new URL('../src/components/data-workbench.tsx',import.meta.url));
  const table={table_name:'sales',rows:2,columns:['price'],dtypes:{price:'VARCHAR'},null_counts:{price:0},history:[],data_version:'abc'};
  const preview={before_rows:2,after_rows:2,affected_rows:2,conversion_failures:1,conflicting_keys:0,examples:[],
    metrics_before:{facts:[]},metrics_after:{facts:[],warnings:[]}};
  const states={0:{tables:[table],snapshot:'abc',commerce:{}},2:'sales',12:preview,13:''};
  let index=0;
  const component={exports:{}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(filename,'utf8'),{
    compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,esModuleInterop:true},
  }).outputText,{module:component,exports:component.exports,require:name=>name==='react'?{...React,useState:initial=>[states[index++]??initial,()=>{}],useRef:()=>({current:true}),useEffect(){}}:
    name==='@/lib/workbench'?compiledWorkbench.exports:name==='@base-ui/react/dialog'?{Dialog:new Proxy({},{get:(_,key)=>key})}:
    name.startsWith('@/')||name.startsWith('./')?new Proxy({},{get:()=>()=>null}):createRequire(filename)(name)});
  const text=node=>node==null?'':typeof node==='string'||typeof node==='number'?String(node):React.Children.toArray(node.props?.children).map(text).join('');
  const content=text(component.exports.DataWorkbench({sessionId:'test',disabled:false,onClose(){},onChanged(){}}));
  assert.match(content,/转换失败 1 条/);
  assert.doesNotMatch(content,/日期转换失败|转换失败的日期将/);
  assert.match(content,/日期或数值/);
});


test('failed preview stays next to its actions without discarding the draft',()=>{
  const filename=fileURLToPath(new URL('../src/components/data-workbench.tsx',import.meta.url));
  const table={table_name:'orders',rows:5,columns:['amount'],dtypes:{amount:'VARCHAR'},null_counts:{amount:1},history:[],data_version:'abc'};
  const states={0:{tables:[table],snapshot:'abc',commerce:{}},2:'orders',3:[{operation:'parse_number',column:'amount'}],13:'',14:'有2条数值格式无法转换'};
  let index=0;const component={exports:{}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,{
    module:component,exports:component.exports,require:name=>name==='react'?{...React,useState:initial=>[states[index++]??initial,()=>{}],useRef:()=>({current:true}),useEffect(){}}:
      name==='@/lib/workbench'?compiledWorkbench.exports:name==='@base-ui/react/dialog'?{Dialog:new Proxy({},{get:(_,key)=>key})}:
      name.startsWith('@/')||name.startsWith('./')?new Proxy({},{get:()=>()=>null}):createRequire(filename)(name),
  });
  const tree=component.exports.DataWorkbench({sessionId:'qa',disabled:false,onClose(){},onChanged(){}});
  const all=node=>!node||typeof node!=='object'?[]:[node,...React.Children.toArray(node.props?.children).flatMap(all)];
  const nodes=all(tree),footer=nodes.find(node=>node.type==='footer');
  const text=node=>node==null?'':typeof node==='string'||typeof node==='number'?String(node):React.Children.toArray(node.props?.children).map(text).join('');
  assert.match(text(footer),/有2条数值格式无法转换/);
  assert.ok(all(footer).some(node=>node.props?.role==='alert'));
  assert.ok(!text(tree).includes('刷新页面重试'));
  assert.ok(text(tree).includes('文本转数值')&&text(tree).includes('amount'));
});
