import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';
import {test} from 'node:test';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

const filename=fileURLToPath(new URL('../src/components/relationship-workbench.tsx',import.meta.url));
const tables=['buyers','purchases','weather'].map((table_name,i)=>{const column=i===2?'humidity':i===1?'customer_ref':'buyer_code';return{table_name,filename:table_name+'.csv',rows:i+2,columns:[column],dtypes:{[column]:'string'},null_counts:{[column]:0},preview:[],source:{kind:i===1?'postgresql':'file',schema:'public',table:table_name}};});
const data={snapshot:'v1',tables,commerce:{profile:null,confirmed:false,warnings:[],facts:[]}};
const base={snapshot:'v1',tables:tables.map(table=>({...table,key_fields:[],grain:'',metadata_status:'not_available'})),candidates:[],reports:[],confirmed:[],understanding:null,scope:'仅检查字段线索，不穷尽业务关系',coverage:{candidate_limit:30,checked_candidates:0,truncated:false,unchecked_candidates:0}};
function harness(request){
 let cursor=0;const states=[],effects=[],compiled={exports:{}};
 const hooks={...React,useRef:()=>({current:null}),useEffect:effect=>effects.push(effect),useState:initial=>{const i=cursor++;if(!(i in states))states[i]=initial;return[states[i],value=>states[i]=typeof value==='function'?value(states[i]):value];}};
 function child(relative){const target=fileURLToPath(new URL(`../src/components/${relative}.tsx`,import.meta.url)),childModule={exports:{}};vm.runInNewContext(ts.transpileModule(fs.readFileSync(target,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,{module:childModule,exports:childModule.exports,require:name=>name==='@/lib/api'?{workbenchRequest:request}:name==='./data-panel'?{ResultTable:()=>null}:createRequire(target)(name)});return childModule.exports;}
 vm.runInNewContext(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,{exports:compiled.exports,module:compiled,Error,require:name=>name==='react'?hooks:name==='@/lib/api'?{workbenchRequest:request}:name==='./table-relationship-map'?child('table-relationship-map'):name==='./table-structure-inspector'?child('table-structure-inspector'):createRequire(filename)(name)});
 const props={sessionId:'qa',data,disabled:false,onUpdated(){},onBusyChange(){}};
 const render=(next={})=>{cursor=0;return compiled.exports.RelationshipWorkbench({...props,...next});};
 return {render,effects,html:next=>renderToStaticMarkup(render(next))};
}
const nodes=node=>!node||typeof node!=='object'?[]:[node,...React.Children.toArray(node.props?.children).flatMap(nodes)];

test('all independent CSV and database tables remain visible without fabricated grain',async()=>{
 const h=harness(async()=>base);h.render();h.effects.forEach(effect=>effect());await new Promise(setImmediate);
 const html=h.html();
 for(const table of tables)assert.ok(html.includes(table.table_name));
 assert.ok(html.includes('当前字段线索未发现可用关联')&&html.includes('表可以独立分析'));
 assert.ok(html.includes('PostgreSQL')&&!html.includes('已确认粒度')&&!html.includes('电商指标口径'));
});

test('AI alias-key understanding shows evidence and strips explanation fields before checking',async()=>{
 const candidate={left_table:'buyers',right_table:'purchases',left_keys:['buyer_code'],right_keys:['customer_ref'],left_grain:'一个客户',right_grain:'一条购买',source:'AI 语义候选',reason:'两边客户编码可能对应'};
 const report={...candidate,snapshot:'v1',cardinality:'一对多',match_status:'matched',left_known_key_rows:2,right_known_key_rows:3,left_keys_unique:true,right_keys_unique:false,left_null_rows:0,right_null_rows:0,left_unmatched_rows:0,right_unmatched_rows:0,left_duplicate_keys:0,right_duplicate_keys:1,actual_join_rows:3,expected_join_rows:2,join_multiplier:1.5,warnings:['全部匹配仍不证明业务含义相同']};
 const understanding={snapshot:'v1',source:'AI 语义推测',tables:[{table_name:'buyers',purpose:'客户记录',grain:'一个客户',reason:'字段线索'}],candidates:[{candidate,report,error:''}],independent_tables:[{table_name:'weather',reason:'缺少共同业务键'}],questions:['是否同一客户编号体系？'],warnings:[],coverage:{tables_included:3,tables_total:3,columns_truncated:false}};
 let understood=false;const requests=[];
 const h=harness(async(sid,action,body)=>{requests.push({action,body});if(action==='relationships/understand'){understood=true;return{task_id:'qa',status:'succeeded',result:{}};}if(action==='relationships/check')return report;return{...base,understanding:understood?understanding:null};});
 h.render();h.effects.forEach(effect=>effect());await new Promise(setImmediate);
 nodes(h.render()).find(node=>node.type==='button'&&node.props.children==='理解表关系').props.onClick();await new Promise(setImmediate);
 let html=h.html();assert.ok(html.includes('AI 粒度推测')&&html.includes('一个客户')&&html.includes('可独立分析'));
 assert.ok(html.includes('已知键行')&&html.includes('1.5 倍')&&html.includes('是否同一客户编号体系'));
 const row=nodes(h.render()).find(node=>node.props?.item?.candidate===candidate);row.props.onChoose(candidate);
 nodes(h.render()).find(node=>node.type==='button'&&node.props.children==='检查这条关联').props.onClick();await new Promise(setImmediate);
 const checked=requests.find(item=>item.action==='relationships/check').body;
 assert.equal(checked.left_table,'buyers');assert.equal(checked.reason,undefined);
 html=h.html({data:{...data,snapshot:'v2'}});
 assert.ok(!html.includes('AI 粒度推测')&&!html.includes('已确认粒度')&&!html.includes('1.5 倍'));
});

test('model failure leaves all table cards and manual checks available',async()=>{
 const h=harness(async(sid,action)=>{if(action==='relationships/understand')throw Error('AI 暂不可用，基础概览仍可使用');return base;});
 h.render();h.effects.forEach(effect=>effect());await new Promise(setImmediate);
 nodes(h.render()).find(node=>node.type==='button'&&node.props.children==='理解表关系').props.onClick();await new Promise(setImmediate);
 const html=h.html();assert.ok(html.includes('AI 暂不可用')&&html.includes('weather')&&html.includes('检查这条关联'));
});
