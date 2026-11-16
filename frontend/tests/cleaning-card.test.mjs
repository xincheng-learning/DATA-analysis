import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

function load(relative, overrides={}) {
  const filename=fileURLToPath(new URL('../src/'+relative,import.meta.url)), compiled={exports:{}};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,
    {module:compiled,exports:compiled.exports,require:name=>overrides[name]||(name==='@/components/cleaning-analysis-impact'?{CleaningAnalysisImpact:()=>null}:createRequire(filename)(name))});
  return compiled.exports;
}

test('cleaning card explains pending changes and conversion loss without claiming execution',()=>{
  const helpers=load('lib/workbench.ts');
  const {CleaningCard}=load('components/cleaning-card.tsx',{'@/lib/api':{},'@/lib/workbench':helpers,'@/components/temporal-evidence':{TemporalEvidence:()=>null},
    '@/components/data-panel':{ResultTable:({rows,columns})=>React.createElement('table',null,React.createElement('tbody',null,rows.map((r,i)=>React.createElement('tr',{key:i},columns.map(c=>React.createElement('td',{key:c},String(r[c])))))) )}});
  const html=renderToStaticMarkup(React.createElement(CleaningCard,{sessionId:'s',initial:{status:'proposed',plan_id:'a'.repeat(32),notes:['金额缺失保留'],questions:[],preview:{table:'orders',rules:[{operation:'trim',column:'state'}],before_rows:20,after_rows:20,affected_rows:3,conversion_failures:2,conflicting_keys:0,examples:[{before:{state:' SP '},after:{state:'SP'}}],before_null_counts:{},after_null_counts:{}},diagnosis:{tables:[]}},disabled:false}));
  assert.ok(html.includes('尚未清洗')&&html.includes('按此方案清洗'));
  assert.ok(html.includes('失败')&&html.includes('2')&&html.includes('空值'));
  assert.ok(html.includes('<table')&&!html.includes('SQL'));
  assert.ok(!html.includes('已清洗成功'));
});

test('number and category rule editor requires explicit parsing and mapping',()=>{
  const {makeCleaningRule}=load('lib/workbench.ts');
  const options={decimal_separator:',',thousands_separator:'.',currency_token:'R$',failure_policy:'reject'};
  assert.equal(makeCleaningRule('parse_number','price','','',[],'first',false,false,options).decimal_separator,',');
  assert.throws(()=>makeCleaningRule('map_values','state','bad json','',[],'first',false,false),/映射|JSON/);
  assert.equal(makeCleaningRule('map_values','state','{"east":"East"}','',[],'first',false,false).mapping.east,'East');
});

test('workflow confirmation uses the task callback and never independently applies cleaning',async()=>{
  let cursor=0,refCursor=0,effects=[],confirmed=0,changed=0;const states=[],refs=[],requests=[];
  const react={...React,useState(initial){const i=cursor++;if(!(i in states))states[i]=initial;return[states[i],v=>states[i]=typeof v==='function'?v(states[i]):v];},useRef(initial){return refs[refCursor++]??={current:initial};},useEffect(fn){effects.push(fn);}};
  const plan={plan_id:'a'.repeat(32),status:'pending',notes:[],questions:[],diagnosis:{tables:[]}};
  const {CleaningCard}=load('components/cleaning-card.tsx',{'react':react,'@/lib/api':{workbenchRequest:async(sid,url)=>{requests.push(url);return plan;}},'@/lib/workbench':load('lib/workbench.ts'),'@/components/temporal-evidence':{TemporalEvidence:()=>null},'@/components/data-panel':{ResultTable:()=>null}});
  const nodes=node=>!node||typeof node!=='object'?[]:[node,...React.Children.toArray(node.props?.children).flatMap(nodes)];
  function render(){cursor=0;refCursor=0;effects=[];return nodes(CleaningCard({initial:plan,sessionId:'s',onChanged:()=>changed++,onConfirmAndContinue:async id=>{assert.equal(id,plan.plan_id);confirmed++;return false;}}));}
  render();effects.forEach(fn=>fn());await new Promise(resolve=>setImmediate(resolve));
  let tree=render();const button=tree.find(n=>n.props?.className==='cleaning-confirm-button');
  assert.equal(button.props.disabled,false);assert.ok(React.Children.toArray(button.props.children).includes('确认清洗并继续原任务'));
  await button.props.onClick();tree=render();
  assert.equal(confirmed,1);assert.equal(changed,0);assert.equal(requests.filter(url=>url.endsWith('/apply')).length,0);
  assert.ok(tree.some(n=>n.props?.role==='alert'&&n.props.children.includes('尚未提交成功')));
});
