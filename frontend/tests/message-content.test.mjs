import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';
import {test} from 'node:test';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import Markdown from 'react-markdown';
import gfm from 'remark-gfm';

function load(relative,overrides={},globals={}) {
  const filename=fileURLToPath(new URL(`../src/${relative}`,import.meta.url));
  const compiled={exports:{}};
  const {outputText}=ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,esModuleInterop:true}});
  const workflow={'@/components/analysis-narrative':{AnalysisNarrative:()=>null},'@/components/clarification-card':{ClarificationCard:()=>null},'@/components/sql-proposal-card':{SqlProposalCard:()=>null},'@/components/metric-context-card':{MetricContextCard:()=>null},'@/components/verification-checks':{VerificationChecks:()=>null,VerificationWarnings:()=>null,DeliveryTrustPanel:()=>null}};
  vm.runInNewContext(outputText,{exports:compiled.exports,module:compiled,require:name=>overrides[name] || workflow[name] || (name.startsWith('@/lib/')&&name!=='@/lib/api'?load('lib/'+name.slice(6)+'.ts'):createRequire(filename)(name)),...globals});
  return compiled.exports;
}
function markdown() {return load('components/message-markdown.tsx',{
  'react-markdown':{__esModule:true,default:Markdown},'remark-gfm':{__esModule:true,default:gfm},
});}

test('table guides keep translated fields, zero samples and missing impacts inside their source table',()=>{
  const {DataDictionary}=load('components/data-overview.tsx',{'@/components/data-panel':{ResultTable:()=>null}});
  const tables=['orders','payments'].map((name,index)=>({table_name:name,title:index?'支付表':'订单表',description:'本表的用途。',rows:10,filename:name+'.csv',preview:[],
    fields:[{column:'amount',label:index?'支付金额':'商品金额',meaning:'本表定义。',dtype:'DOUBLE',missing:index?0:2,missing_percent:index?0:20,sample:0,impact:index?'':'订单金额仅覆盖已知金额。'}]}));
  const html=renderToStaticMarkup(React.createElement(DataDictionary,{tables,nextSteps:['确认金额口径。']}));
  const orders=html.slice(html.indexOf('data-table-name="orders"'),html.indexOf('data-table-name="payments"'));
  assert.ok(orders.includes('商品金额')&&orders.includes('2 行（20%）')&&orders.includes('订单金额仅覆盖已知金额'));
  assert.ok(orders.includes('field-has-missing')&&orders.includes('title="0">0'));
  assert.ok(html.includes('支付金额')&&html.includes('确认金额口径'));
  assert.ok(!html.includes('实际缺失字段及影响'));
});

test('live replies keep partial prose visible without running the final-answer compactor',()=>{
  const {ChatMessage}=load('components/chat-message.tsx',{
    '@/lib/analysis':{compactAnswer:()=>assert.fail('live text must not be compacted')},
    '@/components/data-panel':{ResultTable:()=>null},
    '@/components/message-markdown':markdown(),'@/components/data-overview':{DataOverview:()=>null},
    '@/components/analysis-evidence':{AnalysisEvidence:()=>null},'@/components/cleaning-card':{CleaningCard:()=>null},'@/lib/api':{},
  });
  const html=renderToStaticMarkup(React.createElement(ChatMessage,{role:'assistant',content:'正在解释第一项结论。',metadata:{streaming:true}}));
  assert.ok(html.includes('正在解释第一项结论')&&html.includes('正在生成'));
  assert.ok(!html.includes('复制内容'));
});

test('analysis narrative keeps numbered reasons and comparison tables visible with exact source links',()=>{
  const data=load('lib/analysis.ts');let target;
  const {AnalysisNarrative}=load('components/analysis-narrative.tsx',{
    '@/lib/analysis':data,'@/components/message-markdown':markdown(),
    '@/components/data-panel':{ResultTable:({rows})=>React.createElement('span',null,JSON.stringify(rows))},
  });
  const report={version:1,findings:[{title:'订单增加而每单金额下降',reason:'同一期间的金额等于订单数乘每单金额。',
    recommendation:'继续核对商品组合。',source_keys:['S1'],evidence_ids:['S1.F4']}],
    sources:[{key:'S1',path:[],label:'月度金额',has_chart:true,scope:'实际查询2行',
      table:{columns:['指标','六月','七月'],rows:[{'指标':'商品金额','六月':0,'七月':null}],shown_rows:1,total_rows:1,caption:'真实对比'}}],
    evidence:[{id:'S1.F4',text:'原始数字保留'}],scope:'仅限当前样本',warnings:[],hypotheses:[],next_checks:[]};
  const props={report,metadata:{chart_spec:{data:[{type:'bar',x:['六月','七月'],y:[0,null]}]}},onViewSource:key=>{target=key;}};
  const html=renderToStaticMarkup(React.createElement(AnalysisNarrative,props));
  assert.ok(html.includes('1. 订单增加而每单金额下降')&&html.includes('同一期间'));
  assert.ok(html.includes('打开完整图表'));
  assert.ok(!html.includes('report-chart-preview')&&!html.includes('图形缩略预览')&&!html.includes('viewBox="0 0 320 104"'));
  assert.ok(html.includes('&quot;六月&quot;:0')&&html.includes('&quot;七月&quot;:null'));
  assert.ok(html.indexOf('真实对比')<html.indexOf('<details'));
  const walk=node=>!node||typeof node!=='object'?[]:[node,...React.Children.toArray(node.props?.children).flatMap(walk)];
  const button=walk(AnalysisNarrative(props)).find(node=>node.type==='button'&&node.props['data-source-key']==='S1');
  button.props.onClick();assert.equal(target,'S1');
});

test('long compound answers keep every substantive section visible',()=>{
  const {ReplyText}=load('components/chat-message.tsx',{
    '@/lib/analysis':{compactAnswer:()=>null},'@/components/message-markdown':markdown(),
    '@/components/analysis-evidence':{AnalysisEvidence:()=>null},'@/components/data-overview':{DataOverview:()=>null},
    '@/components/cleaning-card':{CleaningCard:()=>null},'@/lib/api':{imageUrl:()=>''},'@/components/data-panel':{ResultTable:()=>null},
  });
  const html=renderToStaticMarkup(React.createElement(ReplyText,{content:'表介绍。'.repeat(260)+'\n\n## 缺失影响\n\n金额未知，不能直接当零。'}));
  assert.ok(html.includes('缺失影响') && !html.includes('<details'));
});

test('method explanations retain the source action without repeating a statistics table',()=>{
 const {AnalysisNarrative}=load('components/analysis-narrative.tsx',{'@/lib/analysis':load('lib/analysis.ts'),
  '@/components/message-markdown':markdown(),'@/components/data-panel':{ResultTable:()=>React.createElement('span',null,'STATS_TABLE')}});
 const report={findings:[{title:'统计结果',source_keys:['S1'],evidence_ids:['S1.R1']},
  {title:'检验假设',kind:'method',reason:'H0 为等于指定基准。',source_keys:['S1'],evidence_ids:['S1.E1']}],
  sources:[{key:'S1',path:[],table:{columns:['n','mean'],rows:[{n:61,mean:86.2}]}}],
  evidence:[{id:'S1.R1',kind:'row',text:'n=61'},{id:'S1.E1',kind:'execution_receipt',text:'实际执行'}]};
 const html=renderToStaticMarkup(React.createElement(AnalysisNarrative,{report,metadata:{},onViewSource(){}}));
 assert.equal((html.match(/STATS_TABLE/g)||[]).length,1);
 assert.equal((html.match(/data-source-key="S1"/g)||[]).length,2);
 assert.ok(html.includes('H0 为等于指定基准'));
});

test('a computed summary and unanswered requirements remain visible beside their evidence',()=>{
  const {AnalysisNarrative}=load('components/analysis-narrative.tsx',{'@/lib/analysis':load('lib/analysis.ts'),
    '@/components/message-markdown':markdown(),'@/components/data-panel':{ResultTable:()=>null}});
  const html=renderToStaticMarkup(React.createElement(AnalysisNarrative,{report:{version:1,acceptance_version:1,summary:'金额150 BRL，订单3→5。',
    coverage:[{requirement:'金额和订单',status:'answered',evidence_ids:['S1.R2']},{requirement:'利润',status:'needs_input',reason:'尚无成本证据。'}],
    findings:[],sources:[],evidence:[{id:'S1.R2',text:'2024-03：商品金额150 BRL，订单5。'}]},metadata:{}}));
  assert.ok(html.includes('金额150 BRL')&&html.includes('1/2 项已回答'));
  assert.ok(html.includes('需要补充数据或口径')&&html.includes('尚无成本证据')&&html.includes('<details class="report-coverage" open="">'));
  assert.ok(html.includes('[S1.R2]')&&html.includes('2024-03：商品金额150 BRL，订单5。'));
});

test('linked references and historical claims are never counted as reviewed answers',()=>{
  const {AnalysisNarrative}=load('components/analysis-narrative.tsx',{'@/lib/analysis':load('lib/analysis.ts'),
    '@/components/message-markdown':markdown(),'@/components/data-panel':{ResultTable:()=>null}});
  const report={version:1,acceptance_version:1,coverage:[{requirement:'利润变化',status:'linked',reason:'当前只有商品金额，利润尚不可计算。'}],findings:[],sources:[],evidence:[]};
  const html=renderToStaticMarkup(React.createElement(AnalysisNarrative,{report,metadata:{}}));
  assert.ok(html.includes('0/1 项已回答')&&html.includes('尚有缺口')&&html.includes('利润尚不可计算'));
  assert.ok(!html.includes('结论与实际结果逐项对应')&&!html.includes('分析结论与建议'));
  const legacy=renderToStaticMarkup(React.createElement(AnalysisNarrative,{report:{...report,acceptance_version:undefined,coverage:[{requirement:'利润变化',status:'answered'}]},metadata:{}}));
  assert.ok(legacy.includes('历史证据')&&!legacy.includes('1/1 项已回答'));
});

test('workflow displays each result and real board/export actions',()=>{
  const {ChatMessage}=load('components/chat-message.tsx',{
    '@/lib/analysis':{compactAnswer:()=>null},'@/components/message-markdown':markdown(),
    '@/components/analysis-evidence':{AnalysisEvidence:()=>null},'@/components/data-overview':{DataOverview:()=>null},
    '@/components/cleaning-card':{CleaningCard:()=>null},'@/lib/api':{imageUrl:()=>'',resultExportUrl:()=>'/complete.csv'},'@/components/data-panel':{ResultTable:()=>null},
  });
  const html=renderToStaticMarkup(React.createElement(ChatMessage,{role:'assistant',content:'完整回执',sessionId:'test',onOpenBoard:()=>{},metadata:{workflow:{status:'partial',requirements:['统计','保存','导出'],steps:[
    {id:'sum',tool:'analyze',request:'金额统计',status:'succeeded',result:{answer:'总金额为30'}},
    {id:'board',tool:'board',request:'保存面板',status:'succeeded',result:{answer:'已保存',board:{id:'board',title:'经营',version:2}}},
    {id:'export',tool:'export_result',request:'完整下载',status:'succeeded',result:{answer:'可下载123行',download:{result_id:'result',row_count:123}}},
    {id:'later',tool:'analyze',request:'后续分析',status:'skipped'},
  ]}}}));
  for(const text of ['总金额为30','打开面板','123 行 CSV','尚未执行','尚未全部完成'])assert.ok(html.includes(text),text);
  assert.ok(html.includes('/complete.csv'));
});

test('workflow query results retain complete-result pagination and a SQL entry without a chart',()=>{
  let table;
  const {ChatMessage}=load('components/chat-message.tsx',{
    '@/lib/analysis':{compactAnswer:()=>null},'@/components/message-markdown':markdown(),
    '@/components/analysis-evidence':{AnalysisEvidence:()=>null},'@/components/data-overview':{DataOverview:()=>null},
    '@/components/cleaning-card':{CleaningCard:()=>null},'@/lib/api':{},
    '@/components/data-panel':{ResultTable:props=>{table=props;return null;}},
  });
  const html=renderToStaticMarkup(React.createElement(ChatMessage,{role:'assistant',content:'已查询',sessionId:'s',onViewChart(){},metadata:{workflow:{status:'completed',steps:[
    {id:'query',request:'全部流向',status:'succeeded',result:{sql:'select * from flows',row_count:201,rows:[{amount:10}],columns:['amount'],result_id:'full-result'}},
  ]}}}));
  assert.equal(table.totalRows,201);assert.equal(table.resultId,'full-result');assert.equal(table.sessionId,'s');
  assert.ok(html.includes('查看结果与 SQL'));
});

test('only the current waiting workflow exposes confirmation with continuation',()=>{
  let card;
  const {ChatMessage}=load('components/chat-message.tsx',{
    '@/components/message-markdown':{MessageMarkdown:()=>null},
    '@/components/analysis-evidence':{AnalysisEvidence:()=>null},'@/components/data-overview':{DataOverview:()=>null},
    '@/components/cleaning-card':{CleaningCard:props=>{card=props;return null;}},'@/lib/api':{},'@/components/data-panel':{ResultTable:()=>null},
  });
  const confirm=async()=>true,metadata={workflow:{status:'waiting',steps:[{id:'clean',tool:'cleaning',request:'清洗后继续统计',status:'waiting',result:{cleaning:{plan_id:'a'.repeat(32),status:'pending'}}}]}};
  renderToStaticMarkup(React.createElement(ChatMessage,{role:'assistant',content:'待确认',sessionId:'s',metadata,onConfirmCleaning:confirm}));
  assert.equal(card.onConfirmAndContinue,confirm);assert.ok(!card.disabled);
  renderToStaticMarkup(React.createElement(ChatMessage,{role:'assistant',content:'历史待确认',sessionId:'s',metadata}));
  assert.equal(card.onConfirmAndContinue,undefined);assert.equal(card.disabled,true);
});

test('a recoverable chart exposes only the direct retry action on the latest reply',()=>{
  const {ChatMessage}=load('components/chat-message.tsx',{
    '@/components/message-markdown':{MessageMarkdown:()=>null},'@/components/analysis-evidence':{AnalysisEvidence:()=>null,AnalysisScopeNotice:()=>null},
    '@/components/data-overview':{DataOverview:()=>null},'@/components/cleaning-card':{CleaningCard:()=>null},'@/lib/api':{},'@/components/data-panel':{ResultTable:()=>null},
  });
  const props={role:'assistant',content:'图未完成',metadata:{rows:[{amount:33}],columns:['amount'],result_id:'r',data_version:'v',chart_error:'失败'},onFollowUp:()=>{},originalRequest:'画金额'};
  const latest=renderToStaticMarkup(React.createElement(ChatMessage,{...props,onResume:async()=>true}));
  assert.ok(latest.includes('仅重试绘图')&&!latest.includes('继续未完成部分'));
  const history=renderToStaticMarkup(React.createElement(ChatMessage,props));
  assert.ok(!history.includes('仅重试绘图'));
});

test('source coverage warnings precede conclusions and remain in copied text',async()=>{
  let copied='';const Warning=()=>null;
  const {ChatMessage}=load('components/chat-message.tsx',{
    react:{...React,useState:initial=>[initial,()=>{}]},
    '@/lib/analysis':{compactAnswer:()=>({findings:['已知金额为120'],hypotheses:[],nextChecks:[],warnings:[],details:'原文'})},
    '@/components/message-markdown':{MessageMarkdown:()=>null},'@/components/analysis-evidence':{AnalysisEvidence:()=>null},
    '@/components/data-panel':{ResultTable:()=>null},'@/components/data-overview':{DataOverview:()=>null},'@/components/cleaning-card':{CleaningCard:()=>null},
    '@/lib/api':{imageUrl:()=>''},'@/components/verification-checks':{VerificationWarnings:Warning,VerificationChecks:()=>null,DeliveryTrustPanel:()=>null},
    '@/components/metric-context-card':{MetricContextCard:()=>null},
  },{navigator:{clipboard:{writeText:async text=>{copied=text;}}},setTimeout:()=>0});
  const walk=node=>!node||typeof node!=='object'?[]:[node,...React.Children.toArray(node.props?.children).flatMap(walk)];
  const nodes=walk(ChatMessage({role:'assistant',content:'原文',metadata:{checks:[{id:'coverage',status:'warning',detail:'仅覆盖已知金额，不能当完整总额'}]}}));
  assert.ok(nodes.findIndex(n=>n.type===Warning)<nodes.findIndex(n=>n.props?.className==='conclusion-card'));
  await nodes.find(n=>n.props?.['aria-label']==='复制内容').props.onClick();
  assert.ok(copied.startsWith('仅覆盖已知金额，不能当完整总额'));
});

test('metric references and cross-step evidence stay inspectable and collapsed',()=>{
  const tables=load('components/data-panel.tsx',{'@/lib/api':{}});
  const {AnalysisEvidence}=load('components/analysis-evidence.tsx',{'@/components/data-panel':tables});
  const metadata={metric_context:{version:'commerce-definitions/test',validation:'只是参照，不证明SQL已验证',definitions:[
    {id:'average_order_amount',label:'客单商品金额',definition:'总金额除以总订单数',grain:'order_id',date_basis:'下单日期',template_active:true},
  ]},investigation:{status:'completed',reason:'证据说明',steps:[],synthesis:{evidence:[{id:'S1.R1',text:'总额=300',scope:'本次SQL结果'}]}}};
  const html=renderToStaticMarkup(React.createElement(AnalysisEvidence,{metadata}));
  assert.ok(html.includes('指标参照定义') && html.includes('commerce-definitions/test'));
  assert.ok(html.includes('总金额除以总订单数') && html.includes('S1.R1') && html.includes('总额=300'));
  assert.ok(!/<details[^>]*\sopen(?:[\s=>])/.test(html));
});

test('result display keeps identifiers and source values while making numbers and dates readable',()=>{
  const {ResultTable}=load('components/data-panel.tsx',{'@/lib/api':{}});
  const rows=[{id:'0007',amount:46841.20000000012,month:'2018-01-01T00:00:00',tiny:.000000000123}];
  const before=JSON.stringify(rows);
  const html=renderToStaticMarkup(React.createElement(ResultTable,{rows,columns:['id','amount','month','tiny']}));
  assert.match(html,/>46,841\.2<\/td>/);
  assert.match(html,/>2018-01-01<\/td>/);
  assert.match(html,/0007/);
  assert.ok(html.includes('title="46841.20000000012"'));
  assert.ok(!html.includes('>0</td>'));
  assert.equal(JSON.stringify(rows),before);
});

test('assistant Markdown renders semantic headings, bold, code and real table cells',()=>{
  const {MessageMarkdown}=markdown();
  const html=renderToStaticMarkup(React.createElement(MessageMarkdown,{content:'## 表概览\n\n**订单表**包含 `order_id`。\n\n| 字段 | 缺失数 |\n| --- | ---: |\n| amount | 2 |'}));
  assert.match(html,/<h2>表概览<\/h2>/);
  assert.match(html,/<strong>订单表<\/strong>/);
  assert.match(html,/<code>order_id<\/code>/);
  assert.ok(html.includes('<table>') && html.includes('<td>amount</td>'));
  assert.ok(!html.includes('## 表概览') && !html.includes('**订单表**'));
});

test('Markdown does not execute raw HTML, unsafe links or fetch remote images',()=>{
  const {MessageMarkdown}=markdown();
  const html=renderToStaticMarkup(React.createElement(MessageMarkdown,{content:'<script>alert(1)</script>\n\n[危险](javascript:alert%281%29)\n\n![图片](https://tracker.invalid/private-data)'}));
  assert.ok(!html.includes('<script') && !html.includes('javascript:') && !html.includes('<img'));
  assert.ok(!html.includes('src="https://tracker.invalid'));
});

test('a saved data overview renders real sample rows and exact missing counts',()=>{
  const tables=load('components/data-panel.tsx',{'@/lib/api':{}});
  const {DataOverview}=load('components/data-overview.tsx',{'@/components/data-panel':tables});
  const overview=[{table_name:'orders',filename:'orders.csv',rows:5,columns:['id','amount'],dtypes:{id:'VARCHAR',amount:'DOUBLE'},
    null_counts:{id:0,amount:2},source:{kind:'file'},preview:[{id:'a',amount:10},{id:'b',amount:null},{id:'c',amount:0}]}];
  const html=renderToStaticMarkup(React.createElement(DataOverview,{tables:overview}));
  assert.ok(html.includes('orders') && html.includes('amount') && html.includes('空值'));
  assert.match(html,/2.*40(?:\.0+)?%/s);
  assert.ok(html.includes('导入数据') && html.includes('不是分析 SQL'));
  assert.equal(overview[0].preview[1].amount,null);
});

test('chat shows a result table and a chart link while keeping the chart in the side workspace',()=>{
  const {ChatMessage}=load('components/chat-message.tsx',{
    '@/lib/analysis':{compactAnswer:()=>null},'@/components/message-markdown':markdown(),
    '@/components/analysis-evidence':{AnalysisEvidence:()=>null},'@/components/data-overview':{DataOverview:()=>null},'@/components/cleaning-card':{CleaningCard:()=>null},'@/lib/api':{imageUrl:()=>''},
    '@/components/data-panel':{ResultTable:()=>React.createElement('div',null,'TABLE')},
    '@/components/chart-view':{ChartView:()=>React.createElement('div',null,'INLINE_CHART')},
  });
  const html=renderToStaticMarkup(React.createElement(ChatMessage,{role:'assistant',content:'分析结果',
    metadata:{chart_spec:{data:[{type:'bar'}]},rows:[{amount:10}],columns:['amount'],row_count:1},onViewChart(){}}));
  assert.ok(!html.includes('INLINE_CHART') && html.includes('查询结果预览') && html.includes('打开完整图表'));
});

for(const expanded of [false,true]) test(`inline query table shows ${expanded?'at most fifty':'five'} rows without a duplicate results drawer`,()=>{
  let stateIndex=0;
  const rows=Array.from({length:120},(_,n)=>({n}));
  const {ChatMessage}=load('components/chat-message.tsx',{
    react:{...React,useState:()=>[stateIndex++===2?expanded:false,()=>{}]},
    '@/lib/analysis':{compactAnswer:()=>null},'@/components/message-markdown':markdown(),
    '@/components/analysis-evidence':{AnalysisEvidence:()=>null},'@/components/data-overview':{DataOverview:()=>null},'@/components/cleaning-card':{CleaningCard:()=>null},'@/lib/api':{imageUrl:()=>''},
    '@/components/data-panel':{ResultTable:({rows})=>React.createElement('table',null,React.createElement('tbody',null,rows.map(row=>React.createElement('tr',{key:row.n},React.createElement('td',null,row.n)))))},
  });
  const html=renderToStaticMarkup(React.createElement(ChatMessage,{role:'assistant',content:'结果',metadata:{rows,columns:['n'],row_count:120}}));
  assert.equal((html.match(/<tr>/g)||[]).length,expanded?50:5);
  assert.ok(html.includes(expanded?'收起表格':'展开表格'));
  assert.ok(!html.includes('message-result') && !html.includes('下方展开') && !html.includes('再展开'));
});



test('catalog answers remain visible beside table dictionaries and analysis reports',()=>{
  const {ChatMessage}=load('components/chat-message.tsx',{
    '@/lib/analysis':{compactAnswer:()=>null},'@/components/data-panel':{ResultTable:()=>null},
    '@/components/message-markdown':markdown(),
    '@/components/data-overview':{DataOverview:()=>null,DataDictionary:()=>React.createElement('div',null,'SALES_DICTIONARY')},
    '@/components/analysis-narrative':{AnalysisNarrative:()=>React.createElement('div',null,'ANALYSIS_RESULT')},
    '@/components/analysis-evidence':{AnalysisEvidence:()=>null},'@/components/cleaning-card':{CleaningCard:()=>null},'@/lib/api':{},
  });
  for(const extra of [{data_dictionary:[{table_name:'sales'}]},{analysis_report:{version:1}}]){
    const html=renderToStaticMarkup(React.createElement(ChatMessage,{role:'assistant',content:'HIDDEN_DUPLICATE',
      metadata:{...extra,metric_context:{requested_answer:'利润需要实际成本；收付净额不能替代利润。'}}}));
    assert.ok(html.includes('利润需要实际成本'));
    assert.ok(html.includes('SALES_DICTIONARY')||html.includes('ANALYSIS_RESULT'));
    assert.ok(!html.includes('HIDDEN_DUPLICATE'));
    assert.equal(html.split('利润需要实际成本').length,2);
  }
});
