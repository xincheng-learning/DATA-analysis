import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {test} from 'node:test';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

const filename=fileURLToPath(new URL('../src/components/temporal-evidence.tsx',import.meta.url));
const compiled={exports:{}};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,{
  module:compiled,exports:compiled.exports,require:name=>name==='@/lib/api'?{}:name==='@/lib/analysis'?{businessLabel:value=>value}:name==='@/components/data-panel'?{
    ResultTable:({rows})=>React.createElement('div',null,JSON.stringify(rows))}:createRequire(filename)(name),
});
const {TemporalEvidence}=compiled.exports;
const checks=[{id:'buy__approval',earlier_column:'order_purchase_timestamp',later_column:'order_approved_at',eligible_rows:100,reversed_rows:14,unavailable_rows:5,missing_rows:2,invalid_rows:3,
  examples:[{active_row:27,order_id:'0007',earlier:'2018-05-03',later:'2018-05-02'}]},
  {id:'buy__carrier',earlier_column:'order_purchase_timestamp',later_column:'order_delivered_carrier_date',eligible_rows:100,reversed_rows:82,unavailable_rows:5,examples:[]}];
const base={sessionId:'qa',table:'orders',snapshot:'old',currentSnapshot:'old',checks,summary:{unit:'rows',affected_rows:83,multiple_rule_rows:14,overlaps:[{check_ids:['buy__approval','buy__carrier'],rows:14}]}};

test('temporal evidence keeps row union, overlap and actual source values together',()=>{
  const html=renderToStaticMarkup(React.createElement(TemporalEvidence,base));
  assert.ok(html.includes('去重命中 83 行')&&html.includes('多规则重叠 14 行'));
  assert.ok(html.includes('原表行号')&&html.includes('27')&&html.includes('0007')&&html.includes('2018-05-02'));
  assert.ok(html.includes('查看全部 82 行命中')&&html.includes('查看去重后的全部 83 行'));
  assert.ok(html.includes('无空值但格式无效 3')&&html.includes('不等于去重订单数'));
});

test('stale source versions retain saved samples but disable current-row lookup',()=>{
  const html=renderToStaticMarkup(React.createElement(TemporalEvidence,{...base,currentSnapshot:'new'}));
  assert.ok(html.includes('当前版本已变化')&&html.includes('0007'));
  assert.equal((html.match(/disabled=""/g)||[]).length,3);
});

test('legacy evidence and zero matches do not fabricate unions or successful comparisons',()=>{
  const html=renderToStaticMarkup(React.createElement(TemporalEvidence,{...base,summary:undefined,checks:[{...checks[0],id:undefined,reversed_rows:0,eligible_rows:0,unavailable_rows:5,examples:[]}]}));
  assert.ok(!html.includes('去重命中')&&!html.includes('查看全部'));
  assert.ok(html.includes('可比较 0 行')&&html.includes('未参与 5 行'));
});
