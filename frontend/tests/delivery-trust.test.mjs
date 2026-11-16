import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';
import {test} from 'node:test';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

function load(relative,overrides={}) {
  const filename=fileURLToPath(new URL(`../src/${relative}`,import.meta.url));
  const compiled={exports:{}};
  const {outputText}=ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2017,jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,esModuleInterop:true}});
  vm.runInNewContext(outputText,{exports:compiled.exports,module:compiled,require:name=>overrides[name]||(name.startsWith('@/lib/')?load('lib/'+name.slice(6)+'.ts'):createRequire(filename)(name))});
  return compiled.exports;
}

const trust=load('lib/delivery-trust.ts');

const preparation=(requirements,input_coverage=null)=>({analysis_process:{preparation:{data_version:'v1',readiness:{
  requirements,input_coverage,source_completeness:'unverified'}}}});

test('limited preparation remains visible beside successful calculation and reusable scope',()=>{
  const metadata={...preparation([{requirement_id:'R1',requirement:'金额',status:'limited',reason:'金额有未知值，不能当完整总额'}],
    {input_rows:2,missing_rows:{amount:1}}),columns:['amount'],rows:[{amount:10}],
    analysis_brief:{scope_sources:[{text:'未知不补零'}]}};
  const flow=trust.deliveryWorkflow(metadata);
  assert.equal(trust.deliveryOutcome(metadata).status,'limited');
  assert.equal(flow.preparation[0].status,'limited');
  assert.deepEqual(Array.from(flow.declarations),['未知不补零']);
  const {DeliveryTrustPanel}=load('components/verification-checks.tsx');
  const html=renderToStaticMarkup(React.createElement(DeliveryTrustPanel,{checks:[],metadata}));
  for(const text of ['数据准备','人工判断','执行结果','核验范围','不能当完整总额','筛选输入 2 行','amount：1 行未知','未知不补零'])assert.ok(html.includes(text),text);
});

test('missing inputs and internal preparation failure never masquerade as complete or business refusal',()=>{
  assert.equal(trust.deliveryOutcome({...preparation([{status:'needs_input',reason:'缺成本'}]),columns:['amount'],rows:[{amount:10}]}).status,'partial');
  assert.equal(trust.deliveryOutcome({...preparation([{status:'blocked',reason:'内部字段漏填'}]),route:'refuse'}).status,'failed');
  assert.equal(trust.deliveryOutcome({route:'refuse',error:'计算计划无法执行',checks:[{id:'analysis_contract',status:'failed'}]}).status,'failed');
  assert.equal(trust.deliveryOutcome({...preparation([{status:'needs_decision',reason:'是否扣退款'}]),route:'clarify'}).status,'needs_input');
});

test('historical delivery takes precedence over old successes and keeps recomputation outcome',()=>{
  const outcome=trust.deliveryOutcome({columns:['amount'],rows:[{amount:10}],analysis_scope_state:{
    status:'data_changed',message:'原数据已变，重算失败',recalculation_status:'failed'}});
  assert.equal(outcome.status,'historical');assert.match(outcome.detail,/重算失败/);
});

test('workflow preparation includes child limits but excludes superseded evidence',()=>{
  const result=trust.deliveryWorkflow({workflow:{steps:[
    {result:preparation([{requirement:'旧问题',status:'blocked'}]),superseded:true},
    {result:preparation([{requirement:'当前金额',status:'limited',reason:'缺失覆盖有限'}])}
  ]}});
  assert.equal(result.preparation.length,1);assert.equal(result.preparation[0].requirement,'当前金额');
  assert.equal(trust.deliveryWorkflow({}).preparation.length,0);
});

test('preparation alone stays visible and stopped or obsolete work cannot be resumed as current',()=>{
  assert.equal(trust.shouldShowDeliveryTrust({metadata:preparation([{status:'ready'}])}),true);
  assert.equal(trust.deliveryOutcome({workflow:{status:'stopped'}}).status,'stopped');
  assert.equal(trust.canResumeDelivery({data_version:'v',workflow:{status:'stopped',plan:{}},analysis_scope_state:{status:'definition_changed'}}),false);
  const {DeliveryTrustPanel}=load('components/verification-checks.tsx');
  const html=renderToStaticMarkup(React.createElement(DeliveryTrustPanel,{checks:[],metadata:{
    ...preparation([{status:'ready'}]),data_version:'v2',cleaning:{plan_id:'p'}}}));
  assert.match(html,/属于历史数据版本/);assert.match(html,/预览试算不代表已切换/);
});

test('direct continuation requires a saved version and excludes waiting confirmations',()=>{
  assert.equal(trust.canResumeDelivery({chart_error:'中断',result_id:'r',columns:['amount'],data_version:'v'}),true);
  assert.equal(trust.canResumeDelivery({chart_error:'中断',columns:['amount'],data_version:'v'}),false);
  assert.equal(trust.canResumeDelivery({data_version:'v',workflow:{status:'stopped',plan:{}}}),true);
  assert.equal(trust.canResumeDelivery({data_version:'v',workflow:{status:'waiting',plan:{}}}),false);
  assert.equal(trust.canResumeDelivery({workflow:{status:'partial',plan:{}}}),false);
  assert.equal(trust.canResumeDelivery({chart_error:'中断',result_id:'r',columns:['amount'],data_version:'v',analysis_scope_state:{status:'data_changed'}}),false);
  assert.equal(trust.canResumeDelivery({chart_error:'中断',result_id:'r',columns:['amount'],data_version:'v',validation_failure:'计算未通过'}),false);
});

test('delivery outcome distinguishes partial results, missing inputs, refusal and cancellation',()=>{
  assert.equal(trust.deliveryOutcome({route:'refuse',error:'不支持的因果证明'}).status,'refused');
  assert.equal(trust.deliveryOutcome({route:'chat',analysis_brief:{metric_dependencies:[{status:'needs_input'}]}}).status,'needs_input');
  assert.equal(trust.deliveryOutcome({route:'viz',columns:['x'],rows:[{x:1}],chart_error:'图表生成失败'}).status,'partial');
  assert.equal(trust.deliveryOutcome({analysis_scope:{result_status:'cancelled'}}).status,'stopped');
  assert.equal(trust.deliveryOutcome({route:'python',error:'Python 隔离环境尚未启动'}).status,'failed');
  assert.equal(trust.deliveryOutcome({workflow:{status:'waiting',steps:[{status:'skipped'}]}}).label,'等待确认 · 原任务已保留');
  assert.equal(trust.shouldShowDeliveryTrust({metadata:{route:'refuse'}}),true);
  assert.equal(trust.deliveryOutcome({workflow:{status:'completed',steps:[
    {status:'failed',superseded:true,result:{error:'old'}},
    {status:'completed',result:{columns:['x'],rows:[{x:1}]}}
  ]}}).status,'computed');
});

test('a failed result save cannot promise a complete CSV download',()=>{
  const {ResultTable}=load('components/data-panel.tsx',{'@/lib/api':{resultExportUrl:()=>'/result.csv'}});
  const props={columns:['x'],rows:[{x:1}],totalRows:100,sessionId:'s',resultId:'r'};
  const complete=renderToStaticMarkup(React.createElement(ResultTable,props));
  const failed=renderToStaticMarkup(React.createElement(ResultTable,{...props,resultError:'保存失败'}));
  assert.ok(complete.includes('下载完整结果'));
  assert.ok(complete.includes('不等于整份原始数据'));
  assert.ok(!failed.includes('下载完整结果'));
});

test('changed-scope history retains numeric originals and failed or pending recomputation state',()=>{
  const {withScopeHistory}=load('lib/analysis.ts');
  const messages=[{id:1,role:'assistant',content:'五月金额40',metadata:{rows:[{amount:40}]}},
    {id:2,role:'assistant',content:'重算失败',metadata:{analysis_scope:{revises_message_ids:[1],changed_fields:['时间范围'],result_status:'failed'}}}];
  const before=JSON.stringify(messages),view=withScopeHistory(messages);
  assert.equal(view[0].metadata.analysis_scope_state.recalculation_status,'failed');
  assert.equal(view[0].metadata.rows[0].amount,40);
  assert.equal(JSON.stringify(messages),before);
  assert.equal(withScopeHistory(messages,{revises_message_ids:[1],changed_fields:['时间范围']})[0].metadata.analysis_scope_state.recalculation_status,'pending');
});

test('scope notice keeps original conditions and warns before old numbers are reused',()=>{
  const {AnalysisScopeNotice}=load('components/analysis-evidence.tsx',{'@/components/data-panel':{ResultTable:()=>null}});
  const html=renderToStaticMarkup(React.createElement(AnalysisScopeNotice,{metadata:{
    analysis_scope:{version:1,metric_labels:['商品金额'],conditions:[{key:'period',text:'2018年5月'}]},
    analysis_scope_state:{status:'historical_scope',message:'新条件重算失败，尚未取得替代结果。',changed_fields:['时间范围']},
  }}));
  assert.ok(html.includes('原条件的历史结果')&&html.includes('2018年5月')&&html.includes('商品金额')&&html.includes('重算失败'));
});

test('historical chart stays readable beside its original-scope warning in the workspace',()=>{
  const {AnalysisScopeNotice}=load('components/analysis-evidence.tsx',{'@/components/data-panel':{ResultTable:()=>null}});
  const {AnalysisWorkspace}=load('components/analysis-workspace.tsx',{
    '@/lib/analysis':load('lib/analysis.ts'),'@/lib/chart':{chartTitle:()=> '五月商品金额'},
    '@/components/analysis-evidence':{AnalysisEvidence:()=>null,AnalysisScopeNotice},
    '@/components/chart-view':{ChartView:({spec})=>React.createElement('div',null,`SAVED_CHART_${spec.data[0].y[0]}`)},
    '@/components/sql-timeline':{SqlTimeline:()=>null},'@/components/data-panel':{ResultTable:()=>null},
  });
  const html=renderToStaticMarkup(React.createElement(AnalysisWorkspace,{messages:[{role:'assistant',content:'五月商品金额40',metadata:{
    chart_spec:{data:[{y:[40]}]},analysis_scope:{version:1,conditions:[{key:'period',text:'五月'}]},
    analysis_scope_state:{status:'historical_scope',message:'新条件重算失败，尚未取得替代结果。'},
  }}],progress:null,selection:0,onSelect(){},onJump(){}}));
  assert.ok(html.includes('SAVED_CHART_40')&&html.includes('原条件的历史结果')&&html.includes('五月'));
  assert.ok(html.indexOf('原条件的历史结果')<html.indexOf('SAVED_CHART_40'));
});

const passed={id:'calculation_contract',status:'passed',detail:'符合计划'};
const failed={id:'calculation_contract',status:'failed',detail:'结果不符'};
const numeric=(checks=[])=>({rows:[{amount:12}],columns:['amount'],row_count:1,checks});

test('compiled execution is visible without claiming independent numeric verification',()=>{
  const compiled={id:'compiled_execution',status:'passed'};
  const result=trust.buildDeliveryTrust({metadata:numeric([compiled,{id:'calculation_contract',status:'not_checked'}])});
  assert.equal(result.layers[2].status,'按固定计划执行');
  assert.equal(result.layers[2].tone,'partial');
  assert.match(result.unverifiedWarning,/未独立数值复算/);
  const mixed=trust.buildDeliveryTrust({metadata:{workflow:{steps:[
    {result:numeric([compiled])},{result:numeric([])},
  ]}}});
  assert.equal(mixed.layers[2].status,'未独立核对');
  assert.equal(trust.buildDeliveryTrust({metadata:numeric([compiled,failed])}).layers[2].tone,'fail');
});

test('a later failure overrides an earlier pass and survives check order',()=>{
  for(const checks of [[passed,failed],[failed,passed]]){
    assert.equal(trust.buildDeliveryTrust({checks,metadata:numeric()}).layers[2].tone,'fail');
  }
});

test('explicit empty checks never erase checks saved in metadata',()=>{
  assert.equal(trust.buildDeliveryTrust({checks:[],metadata:numeric([passed])}).layers[2].tone,'ok');
  assert.equal(trust.buildDeliveryTrust({checks:[passed],metadata:numeric([failed])}).layers[2].tone,'fail');
});

test('every delivered workflow result needs its own verification',()=>{
  for(const second of [[failed],[],[passed]]){
    const result=trust.buildDeliveryTrust({checks:[passed],metadata:{workflow:{steps:[
      {status:'succeeded',result:numeric([passed])},{status:'succeeded',result:numeric(second)},
    ]}}});
    assert.equal(result.layers[2].tone,second[0]===failed?'fail':second.length?'ok':'warn');
    if(!second.length)assert.match(result.unverifiedWarning,/未独立核对/);
  }
});

test('saved report receipts are checked individually without top-level checks',()=>{
  const report={version:1,evidence:[
    {kind:'execution_receipt',step:1,checks:[passed]},
    {kind:'execution_receipt',step:2,checks:[]},
  ]};
  assert.equal(trust.buildDeliveryTrust({report}).layers[2].tone,'warn');
  report.evidence[1].checks=[passed];
  assert.equal(trust.buildDeliveryTrust({report}).layers[2].tone,'ok');
});

test('a failed child without rows cannot be hidden by a successful child',()=>{
  const metadata={workflow:{steps:[{status:'succeeded',result:numeric([passed])},
    {status:'failed',result:{checks:[failed]}}]}};
  assert.equal(trust.buildDeliveryTrust({metadata}).layers[2].tone,'fail');
});

test('a verified unrelated metric cannot certify a requested business definition',()=>{
  const metadata={...numeric([passed,{id:'metric_definition',status:'passed',metric_ids:['item_amount']}]),
    metric_ids:['item_amount'],analysis_brief:{acceptance:[{metric_ids:['profit']}]}};
  assert.equal(trust.buildDeliveryTrust({metadata}).layers.find(layer=>layer.id==='business').tone,'unknown');
  metadata.analysis_brief.acceptance=[{metric_ids:['item_amount']}];
  assert.equal(trust.buildDeliveryTrust({metadata}).layers.find(layer=>layer.id==='business').tone,'ok');
  metadata.checks.push({id:'metric_definition',status:'failed',metric_ids:['item_amount']});
  assert.equal(trust.buildDeliveryTrust({metadata}).layers.find(layer=>layer.id==='business').tone,'fail');
});

test('a missing-cost response shows business trust even without numerical output',()=>{
  const metadata={analysis_brief:{acceptance:[{metric_ids:['profit']}],metric_dependencies:[{metric_id:'profit',status:'needs_input'}]}};
  assert.equal(trust.shouldShowDeliveryTrust({metadata}),true);
});

test('nested workflow dependencies stay visible in trust and the metric card',()=>{
  const {MetricContextCard}=load('components/metric-context-card.tsx');
  const metadata={workflow:{steps:[{status:'succeeded',result:{...numeric([passed]),analysis_brief:{
    acceptance:[{metric_ids:['profit']}],metric_dependencies:[{metric_id:'profit',status:'needs_input',reason:'缺少实际成本'}],
  }}}]}};
  assert.equal(trust.buildDeliveryTrust({metadata}).layers.find(layer=>layer.id==='business').tone,'warn');
  const html=renderToStaticMarkup(React.createElement(MetricContextCard,{metadata,context:{definitions:[{id:'profit',label:'利润'}]}}));
  assert.ok(html.includes('利润')&&html.includes('缺少实际成本'));
});

test('actual report metric references remain available beside raw rows',()=>{
  const {MetricContextCard}=load('components/metric-context-card.tsx');
  const metadata=numeric([passed]);
  const report={evidence:[{kind:'execution_receipt',metric_ids:['item_amount']}]};
  const html=renderToStaticMarkup(React.createElement(MetricContextCard,{metadata,report,context:{definitions:[
    {id:'item_amount',label:'商品金额',definition:'不含运费',template_active:true},{id:'profit',label:'无关利润'},
  ]}}));
  assert.ok(html.includes('商品金额')&&html.includes('不含运费')&&!html.includes('无关利润'));
});

test('nested investigation uses active leaves rather than superseded failures',()=>{
  const metadata={workflow:{steps:[{status:'succeeded',result:{investigation:{steps:[
    {...numeric([failed]),status:'succeeded',superseded:true},
    {...numeric([passed]),status:'succeeded'},
  ]}}}]}};
  assert.equal(trust.buildDeliveryTrust({metadata}).layers[2].tone,'ok');
  metadata.workflow.steps[0].result.investigation.steps.push({...numeric(),status:'succeeded'});
  assert.equal(trust.buildDeliveryTrust({metadata}).layers[2].tone,'warn');
});

test('catalog confirmation and answered coverage never certify business semantics',()=>{
  const result=trust.buildDeliveryTrust({checks:[passed],metadata:{...numeric([passed]),
    metric_context:{confirmed:true,definitions:[{id:'profit',template_active:true}]},
    analysis_brief:{acceptance:[{metric_ids:['profit']}],metric_dependencies:[
      {metric_id:'profit',status:'needs_input',missing:['实际商品成本'],reason:'缺少实际商品成本'},
    ]}},report:{acceptance_version:1,coverage:[{status:'answered'}]}});
  assert.match(result.layers[0].label,/问题覆盖/);
  assert.match(result.layers[0].status,/本轮拆解/);
  assert.equal(result.layers.find(layer=>layer.id==='business').tone,'warn');
  assert.match(result.layers.find(layer=>layer.id==='business').status,/需补资料/);
});

test('metric card selects this rounds profit even beyond the first six definitions',()=>{
  const {MetricContextCard}=load('components/metric-context-card.tsx');
  const context={confirmed:true,definitions:[...Array.from({length:7},(_,i)=>({id:`other${i}`,label:`无关指标${i}`})),
    {id:'profit',label:'利润',definition:'收入减成本',template_active:true}]};
  const metadata={analysis_brief:{acceptance:[{metric_ids:['profit']}],metric_dependencies:[
    {metric_id:'profit',status:'needs_input',missing:['实际商品成本'],reason:'缺少实际商品成本，不能计算利润'},
  ]}};
  const html=renderToStaticMarkup(React.createElement(MetricContextCard,{context,metadata}));
  assert.ok(html.includes('利润')&&html.includes('实际商品成本'));
  assert.ok(!html.includes('无关指标')&&!html.includes('模板已绑定'));
  assert.equal(renderToStaticMarkup(React.createElement(MetricContextCard,{context,metadata:{}})),'');
});

test('passed calculation contract maps to verified layers without unverified warning',()=>{
  const result=trust.buildDeliveryTrust({
    checks:[{id:'calculation_contract',status:'passed',detail:'ok'}],
    report:{version:1,acceptance_version:1,coverage:[{requirement:'总额',status:'answered'}]},
    metadata:{analysis_brief:{calculation_plan:{stages:[]}},rows:[{amount:1}]},
  });
  assert.equal(result.layers[0].tone,'ok');
  assert.equal(result.layers[1].status,'已声明');
  assert.equal(result.layers[2].status,'已核对（符合计划）');
  assert.equal(result.unverifiedWarning,undefined);
});

test('numeric rows without calculation contract surface 计算未独立核对',()=>{
  const result=trust.buildDeliveryTrust({
    checks:[],
    report:{version:1,acceptance_version:1,coverage:[{requirement:'退款',status:'answered'},{requirement:'利润',status:'needs_input'}]},
    metadata:{rows:[{refund:5}],row_count:1},
  });
  assert.equal(result.layers[0].tone,'warn');
  assert.match(result.layers[0].status,/部分/);
  assert.equal(result.layers[1].status,'无计划');
  assert.equal(result.layers[2].status,'未独立核对');
  assert.match(result.unverifiedWarning,/计算未独立核对/);
});

test('failed calculation contract is fail tone',()=>{
  const result=trust.buildDeliveryTrust({
    checks:[{id:'calculation_contract',status:'failed',detail:'mismatch'}],
    metadata:{rows:[{x:1}]},
  });
  assert.equal(result.layers[2].tone,'fail');
  assert.equal(result.layers[2].status,'未通过');
});

test('trust strip and metric card render for assistant delivery',()=>{
  const delivery=load('lib/delivery-trust.ts');
  const {DeliveryTrustPanel}=load('components/verification-checks.tsx',{
    '@/lib/api':{},
    '@/lib/delivery-trust':delivery,
  });
  const {MetricContextCard}=load('components/metric-context-card.tsx');
  const trustHtml=renderToStaticMarkup(React.createElement(DeliveryTrustPanel,{
    checks:[],
    report:{version:1,acceptance_version:1,coverage:[{requirement:'总额',status:'answered'}]},
    metadata:{rows:[{amount:12}]},
  }));
  assert.ok(trustHtml.includes('交付可信状态'));
  assert.ok(trustHtml.includes('未独立核对'));
  assert.ok(trustHtml.includes('计算未独立核对'));
  const metricHtml=renderToStaticMarkup(React.createElement(MetricContextCard,{metadata:{analysis_brief:{acceptance:[{metric_ids:['profit']}]}},context:{
    version:'commerce-definitions/test',
    definitions:[{id:'profit',label:'利润',definition:'收入减成本',grain:'order',template_active:false}],
  }}));
  assert.ok(metricHtml.includes('本轮涉及的指标')&&metricHtml.includes('利润')&&metricHtml.includes('待映射或确认'));
  assert.equal(renderToStaticMarkup(React.createElement(MetricContextCard,{context:{}})),'');
});

test('analysis start stays concise without a tutorial or capacity manual',()=>{
  const {AnalysisStart}=load('components/analysis-start.tsx',{'lucide-react':{ArrowUpRight:()=>null,ChartNoAxesCombined:()=>null,ScanLine:()=>null,SearchCheck:()=>null}});
  const empty=renderToStaticMarkup(React.createElement(AnalysisStart,{onChoose:()=>{}}));
  const ready=renderToStaticMarkup(React.createElement(AnalysisStart,{hasData:true,onChoose:()=>{}}));
  for(const html of [empty,ready]){
    assert.ok(!html.includes('四步')&&!html.includes('Superset')&&!html.includes('10 MiB'));
    assert.ok(html.includes('先检查数据')&&html.includes('点击只填入问题'));
  }
});

test('continuation drafts retain the goal and do not duplicate pending confirmations',()=>{
  const goal='比较五月和六月的商品金额';
  const failed=trust.continuationDrafts({error:'Docker unavailable'},goal);
  assert.equal(failed[0].label,'继续未完成部分');assert.ok(failed[0].text.includes(goal));
  assert.ok(failed[0].text.includes('不要重复执行已完成的写入操作'));
  assert.equal(trust.continuationDrafts({columns:['x'],rows:[{x:1}]},goal)[0].label,'修改条件并重算');
  assert.equal(trust.continuationDrafts({clarification:{}},goal).length,0);
  assert.equal(trust.continuationDrafts({workflow:{status:'waiting'}},goal).length,0);
  assert.equal(trust.continuationDrafts({streaming:true,error:'x'},goal).length,0);
});

test('onboarding opens existing inspection and relationships without sending',()=>{
  const {AnalysisStart}=load('components/analysis-start.tsx');
  let inspected=0,related=0,chosen=0;
  const tree=AnalysisStart({hasData:true,onChoose:()=>chosen++,onInspect:()=>inspected++,onRelate:()=>related++});
  const buttons=[];
  function visit(node){if(!node||typeof node!=='object')return;if(node.type==='button')buttons.push(node);React.Children.forEach(node.props?.children,visit);}
  visit(tree);
  buttons.find(button=>button.props.children==='打开体检与清洗').props.onClick();
  buttons.find(button=>button.props.children==='多表？检查关联与粒度').props.onClick();
  assert.equal(inspected,1);assert.equal(related,1);assert.equal(chosen,0);
});

test('print toolbar appears on analysis narrative',()=>{
  const analysis=load('lib/analysis.ts');
  const {AnalysisNarrative}=load('components/analysis-narrative.tsx',{
    '@/lib/analysis':analysis,
    '@/components/message-markdown':{MessageMarkdown:({content})=>React.createElement('div',null,content)},
    '@/components/data-panel':{ResultTable:()=>null},
    'lucide-react':{ArrowUpRight:()=>null,Printer:()=>null},
  });
  const html=renderToStaticMarkup(React.createElement(AnalysisNarrative,{
    report:{version:1,summary:'本月金额稳定',findings:[],evidence:[],coverage:[],warnings:[],hypotheses:[],next_checks:[],scope:'样本内'},
    metadata:{},
  }));
  assert.ok(html.includes('data-print-report')&&html.includes('打印本页报告'));
});


test('fixed business definition receipt distinguishes execution from independent verification',()=>{
  const metadata={columns:['net'],rows:[{net:63}],checks:[{id:'business_definition',status:'passed',definition_versions:{net:'v1'}}]};
  const layer=trust.buildDeliveryTrust({metadata}).layers.find(layer=>layer.id==='business');
  assert.equal(layer.status,'按已确认定义执行 · 未独立复算');
  assert.equal(layer.tone,'partial');
  assert.equal(trust.deliveryOutcome({route:'refuse',checks:[{id:'business_definition',status:'failed'}]}).status,'failed');
});
