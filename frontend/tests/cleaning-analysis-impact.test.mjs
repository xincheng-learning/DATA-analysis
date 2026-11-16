import assert from 'node:assert/strict';
import {test} from 'node:test';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const filename=fileURLToPath(new URL('../src/components/cleaning-analysis-impact.tsx',import.meta.url)),compiled={exports:{}};
vm.runInNewContext(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,
  {module:compiled,exports:compiled.exports,require:name=>name==='@/components/data-panel'?{ResultTable:({rows,columns})=>React.createElement('table',null,React.createElement('tbody',null,rows.map((row,i)=>React.createElement('tr',{key:i},columns.map(c=>React.createElement('td',{key:c},String(row[c])))))))}:createRequire(filename)(name)});
const render=(impact,props={})=>renderToStaticMarkup(React.createElement(compiled.exports.CleaningAnalysisImpact,{impact,...props}));
const impact={phase:'preview',status:'verified',goal:'仅已完成订单金额',source_message_id:7,change_count:1,
  before:{status:'computed',rows:[{amount:20}],input_coverage:{input_rows:1,missing_rows:{amount:0}}},
  after:{status:'computed',rows:[{amount:30}],input_coverage:{input_rows:2,missing_rows:{amount:0}}},
  changes:[{dimensions:{},metric:'amount',before:20,after:30,delta:'10',row_status:'changed'}]};

test('comparison shows scoped samples and actual metric values without claiming cleaning is correct',()=>{
  const html=render(impact);
  assert.match(html,/尚未应用/);assert.match(html,/筛选后的输入行数/);assert.match(html,/消息 7/);
  for(const value of ['20','30','10'])assert.ok(html.includes(`<td>${value}</td>`));
  assert.match(html,/不表示清洗规则或业务解释已经认证/);
});

test('applied and undone comparisons are distinguished from the current version',()=>{
  assert.match(render({...impact,phase:'applied',after_snapshot:'v2'},{state:'applied',currentSnapshot:'v2'}),/已保存新版本复核/);
  assert.match(render({...impact,phase:'applied',after_snapshot:'v2'},{state:'undone',currentSnapshot:'v1'}),/历史清洗影响/);
});

test('unavailable baseline stays unknown and its available counterpart stays a trial',()=>{
  const html=render({...impact,status:'partial',reason:'处理前无法计算',changes:[],before:{status:'unverified',reason:'金额是文本',rows:[],input_coverage:null}});
  assert.match(html,/未核验/);assert.match(html,/处理前暂不能/);assert.match(html,/处理后可用的试算结果/);
  assert.doesNotMatch(html,/指标值未变化/);
});

test('metric comparison retains separate before and after sample tables',()=>{
  const html=render({...impact,before:{...impact.before,samples:[{stage:'sample',columns:['id','amount'],rows:[{id:'before-only',amount:20}]}]},
    after:{...impact.after,samples:[{stage:'sample',columns:['id','amount'],rows:[{id:'after-added',amount:10}]}]}});
  assert.match(html,/处理前样本/);assert.match(html,/处理后样本/);
  assert.match(html,/before-only/);assert.match(html,/after-added/);
});
