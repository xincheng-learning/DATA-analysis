import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';
import {test} from 'node:test';
import ts from 'typescript';
import React from 'react';
const filename=fileURLToPath(new URL('../src/components/advanced-views.tsx',import.meta.url));
function load(overrides={},target=filename){
 const compiled={exports:{}};
 vm.runInNewContext(ts.transpileModule(fs.readFileSync(target,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,{exports:compiled.exports,module:compiled,setInterval:()=>0,clearInterval(){},require:name=>overrides[name]||(name==='@/lib/analysis'?load({},fileURLToPath(new URL('../src/lib/analysis.ts',import.meta.url))):name.startsWith('@/')?{}:createRequire(filename)(name))});
 return compiled.exports;
}
const {advancedSources}=load();
const elements=node=>!node||typeof node!=='object'?[]:[node,...React.Children.toArray(node.props?.children).flatMap(elements)];

test('nested investigation sources preserve paths despite repeated IDs and omit failed or superseded steps',()=>{
 const leaf=n=>({step_id:'same',status:'succeeded',sql:'SELECT saved',columns:['n'],rows:[{n}],row_count:61,result_id:'a'.repeat(32),data_version:'v1'});
 const metadata={workflow:{steps:[{id:'second',status:'succeeded',result:{investigation:{steps:[{...leaf(900),superseded:true},leaf(200),{...leaf(999),status:'failed'}]}}},
  {id:'first',status:'succeeded',result:{investigation:{steps:[leaf(100)]}}}]}};
 const sources=advancedSources([{id:31,role:'assistant',metadata}]);
 assert.deepEqual(Array.from(sources,item=>[Array.from(item.source_path||[]),item.data.rows[0].n]),[
  [['workflow','steps',0,'result','investigation','steps',1],200],
  [['workflow','steps',1,'result','investigation','steps',0],100]]);
});
test('advanced sources use saved current-chat IDs and successful complete result grains',()=>{
 const data={sql:'select region,sum(amount) as amount from sales group by region',columns:['region','amount'],rows:[{region:'A',amount:10}],data_version:'v1',row_count:1};
 const messages=[{id:7,role:'assistant',metadata:data},{id:8,role:'user',metadata:data},
 {id:9,role:'assistant',metadata:{...data,error:'failed'}},{id:10,role:'assistant',metadata:{...data,row_count:5001}},
 {id:11,role:'assistant',metadata:{workflow:{steps:[{id:'query',status:'succeeded',result:data},{id:'failed',status:'failed',result:data}]}}},
 {id:12,role:'assistant',metadata:{...data,validation_failure:'口径核对未通过'}},
 {id:13,role:'assistant',metadata:{...data,output_limit_reached:true}},
 {id:14,role:'assistant',metadata:{...data,row_count:61}},
 {id:15,role:'assistant',metadata:{...data,result_id:'a'.repeat(32),result_error:'保存失败'}}];
 const sources=advancedSources(messages);
 assert.deepEqual(Array.from(sources,item=>[item.message_id,item.step_id]),[[7,''],[11,'query']]);
 assert.equal(sources[1].data.sql,data.sql);
});

test('only a computed Python table with a tool receipt can become an advanced source',()=>{
 const data={python_code:'result = computed_table',columns:['month','amount'],rows:[{month:'2018-07',amount:0}],data_version:'v1',row_count:1,
   checks:[{id:'python_result',status:'passed'}]};
 const sources=advancedSources([{id:21,role:'assistant',metadata:data},{id:22,role:'assistant',metadata:{...data,checks:[]}}]);
 assert.deepEqual(Array.from(sources,s=>s.message_id),[21]);assert.equal(sources[0].data.rows[0].amount,0);
});

test('reopened pivot defaults to native and readback carries all-row pagination and CSV',async()=>{
 const saved={id:'b'.repeat(32),title:'61组透视',status:'ready',chart_type:'pivot_table',checks:[],dimensions:['region'],measure:'amount',row_count:61};
 let cursor=0;const states=[],effects=[];
 const {AdvancedViews}=load({react:{...React,useRef:()=>({current:null}),useEffect:effect=>effects.push(effect),useState:initial=>{const i=cursor++;if(!(i in states))states[i]=initial;return[states[i],value=>states[i]=typeof value==='function'?value(states[i]):value];}},
  '@/lib/api':{resultExportUrl:(sid,id)=>`/csv/${sid}/${id}`,localRequest:async path=>path==='/superset/status'?{status:'ready'}:path.includes('/read?session_id=s')?{rows:[{region:'A',amount:0}],columns:['region','amount'],row_count:61,result_id:'c'.repeat(32),answer:'已核对'}:[saved]},
  '@/components/data-panel':{ResultTable:()=>null}});
 const props={sessionId:'s',messages:[],initialId:saved.id,onClose(){},onAnalyse:async()=>{}};
 const render=()=>{cursor=0;return elements(AdvancedViews(props));};
 render();effects[0]();await new Promise(setImmediate);
 assert.equal(render().find(node=>node.type==='button'&&node.props.children==='Superset 原生').props['aria-pressed'],true);
 render().find(node=>node.type==='button'&&node.props.children==='核对默认数据').props.onClick();await new Promise(setImmediate);
 const table=render().find(node=>node.props?.resultId==='c'.repeat(32));
 assert.equal(table.props.totalRows,61);assert.equal(table.props.sessionId,'s');
 assert.ok(render().some(node=>node.type==='a'&&node.props.href==='/csv/s/'+'c'.repeat(32)));
});


test('a completed creation replaces its pending poll entry instead of displaying a duplicate',async()=>{
 const saved={id:'a'.repeat(32),title:'地区树图',status:'ready',checks:[],dimensions:['region'],measure:'amount',row_count:1};
 let cursor=0;const states=[],effects=[];
 const {AdvancedViews}=load({react:{...React,useRef:()=>({current:null}),useEffect:effect=>effects.push(effect),useState:initial=>{const i=cursor++;if(!(i in states))states[i]=initial;return[states[i],value=>states[i]=typeof value==='function'?value(states[i]):value];}},
  '@/lib/api':{localRequest:async(path,method)=>path==='/superset/status'?{status:'ready'}:method==='POST'?saved:[{...saved,status:'creating'}]},
  '@/lib/analysis':{...load({},fileURLToPath(new URL('../src/lib/analysis.ts',import.meta.url))),businessLabel:value=>value},'@/components/data-panel':{ResultTable:()=>null}});
 const props={sessionId:'s',messages:[{id:1,role:'assistant',metadata:{sql:'select region,amount from sales',columns:['region','amount'],rows:[{region:'A',amount:10}],row_count:1,data_version:'v1'}}],onClose(){},onAnalyse:async()=>{}};
 const render=()=>{cursor=0;return elements(AdvancedViews(props));};
 render();effects[0]();await new Promise(setImmediate);
 render().find(node=>node.props?.['aria-label']==='高级图表来源').props.onChange({target:{value:'1:'}});
 render().find(node=>node.props?.type==='checkbox').props.onChange({target:{checked:true}});
 render().find(node=>node.type==='form').props.onSubmit({preventDefault(){}});
 await new Promise(setImmediate);
 const titles=render().filter(node=>node.type==='strong'&&node.props.children===saved.title);
 assert.equal(titles.length,1);
});
