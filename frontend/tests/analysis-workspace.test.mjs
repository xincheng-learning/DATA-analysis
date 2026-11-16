import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

function load(relative, overrides = {}) {
  const filename = fileURLToPath(new URL(`../src/${relative}`, import.meta.url));
  const compiled = { exports: {} };
  const { outputText } = ts.transpileModule(fs.readFileSync(filename, "utf8"), {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, esModuleInterop: true },
  });
  vm.runInNewContext(outputText, { exports: compiled.exports, module: compiled, structuredClone, require: (name) => overrides[name] || (name==='@/components/data-panel'?{ResultTable:()=>null}:name==='@/lib/chart-appearance'?appearance:name==='./chart-appearance-editor'?{ChartAppearanceEditor:()=>null}:name==='@/components/analysis-evidence'?{AnalysisEvidence:()=>null}:name==='@/lib/chart-effects' ? {finishChart(){},downloadChartPng:async()=>{}} : createRequire(filename)(name)) });
  return compiled.exports;
}
const analysis = load("lib/analysis.ts");
test('Python repair receipts retain failed and successful attempts with escaped code',()=>{
  const {AnalysisEvidence}=load('components/analysis-evidence.tsx');
  const html=renderToStaticMarkup(React.createElement(AnalysisEvidence,{metadata:{python_code:'result=61',python_attempts:[
    {code:'<script>bad</script>',error:'KeyError: amount'},{code:'result=61',error:''},
  ]}}));
  assert.match(html,/第 1 次.*失败/);assert.match(html,/第 2 次.*完成/);
  assert.match(html,/KeyError: amount/);assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>/);
});
const appearance=load('lib/chart-appearance.ts',{'./analysis':analysis});
const packed=load('lib/packed-bubbles.ts');
const charts = load("lib/chart.ts", { "./analysis": analysis,'./chart-appearance':appearance,'./packed-bubbles':packed });

test('stacked area fragments preserve real zero and leave unknown periods unpainted',()=>{
  const source={data:[
    {type:'scatter',mode:'lines',x:['2026-01','2026-02','2026-03','2026-04'],y:[100,0,null,160],name:'amount',stackgroup:'same',fill:'tonexty'},
    {type:'scatter',mode:'lines',x:['2026-01','2026-02','2026-03','2026-04'],y:[60,20,null,80],name:'other',stackgroup:'same',fill:'tonexty'}],layout:{}};
  const original=JSON.stringify(source);
  const figure=charts.chartFigure(source,false,900,600);
  assert.equal(figure.data.length,4);
  assert.ok(figure.data.every(trace=>trace.cliponaxis===false),'isolated endpoints must remain fully visible at the plot boundary');
  assert.equal(new Set(figure.data.map(t=>t.stackgroup)).size,2);
  for(let index=0;index<2;index++){
    const pieces=figure.data.filter(t=>t.meta.source_trace_index===index);
    assert.deepEqual(JSON.parse(JSON.stringify(pieces.flatMap(t=>t.meta.source_indices))),[0,1,3]);
    assert.deepEqual(JSON.parse(JSON.stringify(pieces.flatMap(t=>t.y))),index===0?[100,0,160]:[60,20,80]);
    assert.equal(pieces.filter(t=>t.showlegend!==false).length,1);
    assert.equal(pieces[0].marker.color,pieces[1].marker.color);
  }
  assert.equal(JSON.stringify(source),original);
});

test('packed circle coordinates and pixel diameters share the real export plot scale',()=>{
  const spec={data:[{type:'scatter',x:['A','B','C','zero'],y:[1,4,9,0],meta:{family:'packed_bubble'}}],layout:{meta:{packed_bubble:true}}};
  const margins={l:90,r:70,t:110,b:90};
  const figure=charts.chartFigure(spec,false,1400,850,margins),t=figure.data[0];
  const sx=(1400-margins.l-margins.r)/(figure.layout.xaxis.range[1]-figure.layout.xaxis.range[0]);
  const sy=(850-margins.t-margins.b)/(figure.layout.yaxis.range[1]-figure.layout.yaxis.range[0]);
  for(let i=0;i<3;i++){
    const r=t.marker.size[i]/2;
    assert.ok(t.x[i]*sx-r>=-.01&&t.x[i]*sx+r<=1240+.01&&t.y[i]*sy-r>=-.01&&t.y[i]*sy+r<=650+.01);
    for(let j=i+1;j<3;j++)assert.ok(Math.hypot((t.x[i]-t.x[j])*sx,(t.y[i]-t.y[j])*sy)+.01>=(t.marker.size[i]+t.marker.size[j])/2);
  }
});

test('packed bubble areas match independent 1:4:9 reference and circles never overlap',()=>{
  for(const [width,height] of [[900,430],[398,400]]){
    const trace=packed.packedBubbles({x:['A','B','C','zero'],y:[1,4,9,0],meta:{}},width,height);
    const sizes=trace.marker.size;
    assert.ok(Math.abs((sizes[1]/sizes[0])**2-4)<1e-10);
    assert.ok(Math.abs((sizes[2]/sizes[0])**2-9)<1e-10);assert.equal(sizes[3],0);
    for(let i=0;i<4;i++)for(let j=i+1;j<4;j++)assert.ok(Math.hypot(trace.x[i]-trace.x[j],trace.y[i]-trace.y[j])+.01>=(sizes[i]+sizes[j])/2);
    assert.deepEqual(JSON.parse(JSON.stringify(trace.customdata)),[['A',1],['B',4],['C',9],['zero',0]]);
  }
});

test('all-zero bubbles retain zero area and finite positions',()=>{
  const figure=packed.packedBubbles({x:['A','B'],y:[0,0],meta:{}},398,400);assert.deepEqual(JSON.parse(JSON.stringify(figure.marker.size)),[0,0]);assert.ok([...figure.x,...figure.y].every(Number.isFinite));
});

test('horizontal bar thickness depends on chart height, not the width of the page',()=>{
  const spec={data:[{type:'bar',orientation:'h',x:[10,20,30],y:['A','B','C']}],layout:{}};
  assert.equal(charts.chartFigure(spec,true,1400,320).data[0].width,charts.chartFigure(spec,true,500,320).data[0].width);
});

test('ocean colors match tooltip markers, flow links and readable hierarchy labels',()=>{
  const spec={presentation:{palette:'ocean'},data:[{type:'bar',x:['A'],y:[10]}],layout:{}};
  for(const dark of [false,true]){
    const figure=charts.chartFigure(spec,dark,900,650);
    assert.equal(charts.chartTooltip(spec,'A',figure).rows[0].color,figure.data[0].marker.color);
    const tree=charts.chartFigure({presentation:spec.presentation,data:[{type:'treemap',labels:['root','deep','light'],parents:['','root','root'],values:[2,1,1],marker:{colors:['#111111','#111111','#eeeeee']}}]},dark,900,650).data[0];
    assert.equal(tree.textfont.color[1],'#F1F5F9');assert.equal(tree.textfont.color[2],'#091316');
    const flow=charts.chartFigure({presentation:spec.presentation,data:[{type:'sankey',node:{label:['A','B'],color:['#000000','#ffffff']},link:{source:[0],target:[1],value:[10],color:['rgba(0,0,0,.2)']}}]},dark,900,650).data[0];
    assert.equal(flow.link.color[0],charts.chartRgba(flow.node.color[0],.26));
    assert.equal(flow.link.value[0],10);
  }
});

test('independent unit panels keep their axes, readable labels and suppress mixed totals',()=>{
  const spec={data:[{type:'bar',x:['Jun','Jul'],y:[42481.28,50588.49],xaxis:'x',yaxis:'y'},
    {type:'bar',x:['Jun','Jul'],y:[287,359],xaxis:'x2',yaxis:'y2'},
    {type:'bar',x:['Jun','Jul'],y:[148.02,140.92],xaxis:'x3',yaxis:'y3'}],
    layout:{meta:{independent_units:true,unit_panels:3},showlegend:false,xaxis:{},xaxis2:{anchor:'y2'},xaxis3:{anchor:'y3'},
      yaxis:{title:'BRL',domain:[.72,1]},yaxis2:{title:'单',domain:[.38,.66]},yaxis3:{title:'BRL/单',domain:[.05,.33]},
      annotations:[{text:'订单数（单）',xref:'paper',yref:'paper',x:0,y:.66}]}};
  const before=JSON.stringify(spec);
  for(const dark of [true,false]){
    const figure=charts.chartFigure(spec,dark,1400,1000);
    assert.equal(figure.layout.showlegend,false);
    assert.equal(figure.layout.yaxis.title.text,'BRL');
    assert.equal(figure.layout.yaxis2.title.text,'单');
    assert.equal(figure.layout.yaxis3.title.text,'BRL/单');
    assert.equal(figure.layout.yaxis2.tickfont.color,dark?'#64748B':'#43574d');
    assert.deepEqual(Array.from(figure.layout.yaxis3.domain),[.05,.33]);
    assert.ok(figure.layout.annotations[0].font.color);
    assert.equal(figure.layout.annotations.length,1);
    const single=charts.chartFigure({data:[spec.data[0]],layout:{}},dark,1400,1000);
    assert.equal(figure.data[0].width,single.data[0].width);
  }
  assert.equal(charts.chartAnalysis(spec).kpis.length,0);
  assert.equal(charts.chartAnalysis(spec).average,null);
  assert.equal(charts.chartTooltip(spec,'Jun').total,null);
  assert.equal(charts.chartTooltip(spec,'Jun').rows[2].formatted,'R$ 148.02');
  assert.equal(JSON.stringify(spec),before);
});

test('saved presentation changes labels and styling while retaining source values and unknowns',()=>{
  const source={data:[{type:'bar',x:['A','B'],y:[0,null]}],layout:{title:'原标题',yaxis:{title:'金额（BRL）'}}};
  const before=JSON.stringify(source),styled=appearance.applyChartAppearance(source,{title:'经营交付',y_label:'商品金额（BRL）',palette:'ocean',font_size:16,show_legend:false});
  for(const dark of [false,true]){
    const figure=charts.chartFigure(styled,dark,1000,600);
    assert.equal(charts.chartTitle(styled),'经营交付');assert.equal(figure.data[0].y[0],0);assert.equal(figure.data[0].y[1],null);
    assert.equal(figure.layout.font.size,16);assert.equal(figure.layout.showlegend,false);assert.equal(figure.data[0].marker.color,'#245b82');
    assert.ok(figure.layout.annotations.some(a=>a.text==='商品金额（BRL）'));
  }
  assert.equal(JSON.stringify(source),before);assert.equal(charts.chartTitle(appearance.applyChartAppearance(source,{})),'原标题');
});

test('category comparisons do not label their last category as the latest observation',()=>{
  const spec={data:[{type:'bar',x:['SP','CE'],y:[100,20],name:'May'},
                    {type:'bar',x:['SP','CE'],y:[80,40],name:'June'}],layout:{barmode:'group',yaxis:{title:'金额（BRL）'}}};
  const kpi=charts.chartAnalysis(spec).kpis[0];
  assert.equal(kpi.label,'末项展示值');assert.equal(kpi.detail,'CE');assert.equal(kpi.value,'R$ 20');
  const time=charts.chartAnalysis({data:[{type:'scatter',mode:'lines',x:['2018-05','2018-06'],y:[100,80]}],layout:{}});
  assert.equal(time.kpis[0].label,'最新观测');
});

test('readable metric aliases retain explicitly named currency and missing-value meaning',()=>{
  assert.equal(analysis.businessLabel('item_amount_sum_cny'),'商品金额合计（CNY）');
  assert.equal(analysis.businessLabel('null_item_amount_rows'),'商品金额为空的订单数');
  assert.equal(analysis.businessLabel('unknown_field'),'unknown_field');
  assert.equal(analysis.businessLabel('june_item_amount_brl'),'6月商品金额（BRL）');
  assert.equal(analysis.businessLabel('purchase_month'),'下单月份');
});

test('a scalar metrics reply keeps all four requested figures in the concise conclusion',()=>{
  const summary=analysis.compactAnswer('主要发现\n商品金额：50,588.49。\n运费：9,748.1。\n支付金额：60,336.57。\n客单商品金额：140.915014。\n\n数字依据\n真实结果',{row_count:1});
  assert.equal(summary.findings.length,4);
  assert.ok(summary.findings.some(line=>line.includes('客单商品金额')));
});

test('trend summaries retain every requested answer and small nonzero values',()=>{
  const traces=Array.from({length:4},(_,index)=>({type:'scatter',mode:'lines',name:`指标${index+1}`,x:['2026-01','2026-02'],y:[.0000001,.0000002]}));
  const result=analysis.compactAnswer('主要发现\n金额的最大观测值为2。\n独立的第四项回答保留其结果。\n\n数字依据\n真实结果',{chart_spec:{data:traces},row_count:2});
  assert.ok(result.findings.some(line=>line.includes('独立的第四项回答')));
  assert.equal(result.findings.filter(line=>line.startsWith('图中')).length,4);
  assert.ok(result.findings.some(line=>line.includes('0.0000001')));
});

test('complex reports show concise findings with evidence and separate hypotheses and next checks',()=>{
  const summary=analysis.compactAnswer('### 分析结论\n完整的详细报告',{investigation:{synthesis:{status:'generated',
    findings:[{text:'金额增加但客单价下降。',evidence_ids:['S1.F4','S1.R1']},{text:'继续核对订单量。',evidence_ids:['S1.F4']}],
    evidence:[{id:'S1.F4',kind:'change',text:'商品金额增加 200'},{id:'S1.R1',kind:'row',text:'完整结果行，不应重复堆在结论中'}],
    hypotheses:['可能存在订单结构变化。'],next_checks:['按品类继续拆解。'],warnings:[]}}});
  assert.equal(summary.findings.length,2);
  assert.ok(summary.findings[0].includes('商品金额增加 200'));
  assert.ok(!summary.findings.join('').includes('完整结果行'));
  assert.equal(summary.findings.join('').split('商品金额增加 200').length-1,1);
  assert.deepEqual(Array.from(summary.hypotheses),['可能存在订单结构变化。']);
  assert.deepEqual(Array.from(summary.nextChecks),['按品类继续拆解。']);
});

test('concise reports retain computed metric changes omitted by the model',()=>{
  const summary=analysis.compactAnswer('原始说明',{investigation:{synthesis:{status:'generated',
    findings:[{text:'州级变化。',evidence_ids:['S2.R1']}],
    evidence:[{id:'S1.F4',kind:'change',text:'已送达订单数从359变为321'},
              {id:'S2.R1',kind:'row',text:'州=SC；变化=-1785.46'}],warnings:[]}}});
  assert.ok(summary.findings.join('').includes('359') && summary.findings.join('').includes('321'));
});

test('non-additive metrics never get a total or an unweighted average reference', () => {
  for (const title of ['客单价 (BRL)', '平均商品金额 (BRL)', '去重客户数', '复购率']) {
    const summary=charts.chartAnalysis({data:[{type:'bar',x:['一月','二月'],y:[100,200]}],layout:{yaxis:{title}}});
    assert.equal(summary.additive,false,title);
    assert.equal(summary.average,null,title);
    assert.ok(!summary.kpis.some(k=>/总计|均值/.test(k.label)),title);
  }
});

test('percentage scale is explicit and raw values are never guessed from a title', () => {
  const make=scale=>({data:[{type:'bar',name:'转化率',x:['A'],y:[scale==='ratio'?.25:25],meta:{value_scale:scale}}],layout:{yaxis:{title:'占比 (%)'}}});
  assert.equal(charts.chartTooltip(make('percent'),'A').rows[0].formatted,'25%');
  assert.equal(charts.chartTooltip(make('ratio'),'A').rows[0].formatted,'25%');
  assert.equal(charts.chartNumber(25,'占比 (%)'),'25');
  assert.equal(charts.chartAnalysis(make('percent')).additive,false);
});

test('accepted rounding keeps tiny non-zero at ¥ 0 while the raw value stays exact', () => {
  assert.equal(charts.chartNumber(0,'金额 (¥)'),'¥ 0');
  assert.equal(charts.chartNumber(2e-7,'金额 (¥)'),'¥ 0');
  assert.equal(charts.chartNumber(0.0000002,'金额 (¥)'),'¥ 0');
  const tooltip=charts.chartTooltip({data:[{type:'bar',x:['A'],y:[2e-7]}],layout:{yaxis:{title:'金额 (¥)'}}},'A');
  assert.equal(tooltip.rows[0].formatted,'¥ 0');
  assert.equal(tooltip.rows[0].value,2e-7);
});

test('missing series remain explicit and incomplete totals never claim full revenue or shares',()=>{
  const spec={data:[{type:'bar',name:'渠道A',x:['五月','六月'],y:[100,120]},
    {type:'bar',name:'渠道B',x:['五月'],y:[20]}],layout:{barmode:'stack',yaxis:{title:'金额 (BRL)'}}};
  const summary=charts.chartAnalysis(spec);
  assert.equal(summary.kpis[0].label,'图示已知合计');
  assert.equal(summary.average,null);
  assert.ok(!summary.kpis.some(kpi=>kpi.label==='核心贡献'));
  const tooltip=charts.chartTooltip(spec,'六月');
  assert.equal(tooltip.rows.length,2);assert.equal(tooltip.rows[1].value,null);
  assert.ok(tooltip.rows.every(row=>row.share===null));
  assert.ok(charts.chartExportFigure(spec,false).layout.annotations.some(a=>String(a.text).includes('缺失')));
});

test('long high-cardinality labels change ticks, preserve categories and get readable export space',()=>{
  const labels=Array.from({length:60},(_,i)=>`华东区域第${i}家直营门店的跨境业务销售部门`);
  const spec={data:[{type:'bar',orientation:'h',x:labels.map((_,i)=>i+1),y:labels}],layout:{xaxis:{title:'金额 (BRL)'}}};
  const before=JSON.stringify(spec),figure=charts.chartFigure(spec,false,500,1000);
  assert.ok(figure.layout.yaxis.ticktext.some(text=>String(text).includes('<br>')));
  assert.deepEqual(Array.from(figure.data[0].y),labels);
  assert.ok(charts.chartReadability(spec).warnings.some(text=>text.includes('类别')));
  const exported=charts.chartExportFigure(spec,false);
  assert.ok(exported.layout.height>=1200&&exported.layout.margin.l>=180);
  assert.equal(JSON.stringify(spec),before);
});

test('dense flow keeps weights and complete hover names while allowing two-line labels',()=>{
  const labels=Array.from({length:48},(_,i)=>`区域${i}跨境渠道与直营网点的完整业务名称`);
  const spec={data:[{type:'sankey',node:{label:labels},link:{source:[0,1],target:[24,25],value:[10,20]}}],layout:{}};
  const figure=charts.chartExportFigure(spec,false),trace=figure.data[0];
  assert.ok(figure.layout.height>=1000);
  assert.deepEqual(Array.from(trace.link.value),[10,20]);
  assert.ok(trace.link.hovertemplate[0].includes(labels[0])&&trace.link.hovertemplate[0].includes(labels[24]));
  assert.ok(trace.node.hovertemplate[0].includes(labels[0]));
});

test('daily date ticks keep distinguishable day labels instead of collapsing to YYYY-MM', () => {
  const days=Array.from({length:31},(_,i)=>`2026-01-${String(i+1).padStart(2,'0')}`);
  assert.equal(charts.formatChartDateTick('2026-01-15',days),'01-15');
  assert.equal(charts.formatChartDateTick('2026-01-01T08:30:00',days),'01-01 08:30');
  assert.equal(charts.formatChartDateTick('2026-01',['2026-01','2026-02']),'2026-01');
  const figure=charts.chartFigure({data:[{type:'scatter',mode:'lines',x:days,y:days.map((_,i)=>i)}],layout:{yaxis:{title:'金额'}}},false,920,420);
  const labels=figure.layout.xaxis.ticktext;
  assert.ok(labels.length>=3);
  assert.ok(labels.every(label=>/^\d{2}-\d{2}$/.test(String(label))));
  assert.ok(new Set(labels).size===labels.length);
});

test('cleaning confirmation and undo do not count as unrecorded model calls',()=>{
  const usage=analysis.sessionUsage([{role:'assistant',metadata:{route:'cleaning',cleaning_event:{action:'apply'}}},{role:'assistant',metadata:{route:'cleaning',cleaning_event:{action:'undo'}}}]);
  assert.equal(usage.legacy,0);
  assert.equal(usage.total,0);
  assert.equal(usage.missing,0);
});

test('stack summaries use category totals, preserve missing values and never sum unrelated line measures', () => {
  const spec={data:[{type:'bar',name:'SP',x:['2018-01','2018-02'],y:[100,200]},
    {type:'bar',name:'RJ',x:['2018-01','2018-02'],y:[50,null]}],layout:{barmode:'stack',yaxis:{title:'金额 (BRL)'}}};
  const before=JSON.stringify(spec),summary=charts.chartAnalysis(spec);
  assert.equal(summary.average,null);
  assert.equal(summary.kpis[0].value,'R$ 350');
  assert.equal(summary.kpis[0].label,'图示已知合计');
  assert.ok(!summary.kpis.some(k=>k.value.includes('%')));
  assert.ok(summary.scope.includes('缺失'));
  assert.equal(summary.totals[1].value,200);
  assert.equal(JSON.stringify(spec),before);
  const lines=charts.chartAnalysis({data:spec.data.map(t=>({...t,type:'scatter',mode:'lines'})),layout:spec.layout});
  assert.equal(lines.average,150);
  assert.ok(!lines.kpis.some(k=>k.label.includes('总计')));
});

test('shares are omitted for zero totals, negative values and different measurement axes', () => {
  const spec={data:[{type:'bar',name:'A',x:['Jan'],y:[0]},{type:'bar',name:'B',x:['Jan'],y:[0]}],layout:{barmode:'stack'}};
  assert.equal(charts.chartTooltip(spec,'Jan').rows[0].share,null);
  const negative={...spec,data:spec.data.map((t,i)=>({...t,y:[i===0?-2:5]}))};
  assert.equal(charts.chartTooltip(negative,'Jan').rows[1].share,null);
  const mixed={...spec,data:[spec.data[0],{...spec.data[1],yaxis:'y2'}]};
  assert.equal(charts.chartAnalysis(mixed).kpis.length,0);
});

test('dark charts use luminous colors, horizontal measure headings and a category mean reference', () => {
  const source={data:[{type:'bar',name:'SP',x:['2018-01','2018-02'],y:[10,30]},
    {type:'bar',name:'RJ',x:['2018-01','2018-02'],y:[20,40]}],layout:{barmode:'stack',xaxis:{title:'月份'},yaxis:{title:'金额 (BRL)'},shapes:[{type:'line',y0:1,y1:1}]}};
  const fig=charts.chartFigure(source,true,600,360);
  assert.equal(fig.data[0].marker.color,'#10B981');
  assert.equal(fig.data[1].marker.color,'#0EA5E9');
  assert.equal(fig.layout.xaxis.title.text,'');
  assert.equal(fig.layout.yaxis.title.text,'');
  assert.ok(fig.layout.annotations.some(a=>a.text==='金额 (BRL)' && !a.textangle));
  assert.equal(fig.layout.shapes.at(-1).y0,50);
  assert.equal(fig.layout.shapes.length,2);
});

test("analysis presentation keeps a short conclusion and visible warnings with full details available", () => {
  const content='主要发现\nitem_amount 的最大观测值为 180，对应 month=2018-02；最小观测值为 100。\n该期间数据覆盖不完整。\n\n数字依据\nF1 SQL\n\n适用范围\n仅依据已保留的50行';
  const summary=analysis.compactAnswer(content,{sql:'SELECT ...'});
  assert.ok(summary.findings[0].includes('商品金额'));
  assert.ok(summary.warnings.some(line=>line.includes('不完整')));
  assert.ok(summary.details.includes('F1 SQL'));
  assert.equal(analysis.compactAnswer('查询失败',{error:'timeout'}),null);
});

test("heatmap dates are horizontal and rate labels are readable without changing cells", () => {
  const spec={data:[{type:'heatmap',x:['2018-01-01','2018-02-01'],y:['SP'],z:[[.25,null]],
    colorbar:{title:{text:'retention_rate'}}}],layout:{xaxis:{title:'月份'},yaxis:{title:'州'}}};
  const figure=charts.chartFigure(spec,false,420,340);
  assert.equal(figure.layout.xaxis.tickangle,0);
  assert.equal(figure.data[0].colorbar.title.text,'留存率');
  assert.equal(figure.data[0].colorbar.tickformat,'.0%');
  assert.equal(figure.data[0].text[0][0],'25%');
  assert.equal(figure.data[0].z[0][1],null);
  assert.equal(figure.layout.yaxis.autorange,'reversed');
  assert.equal(figure.layout.yaxis.type,'category');
});

test("funnel labels retain enough precision to show small real losses", () => {
  const figure=charts.chartFigure({data:[{type:'funnel',x:[2128,2127],y:['purchase','approved']}]},false,500,340);
  assert.ok(figure.data[0].texttemplate.includes('percentInitial:.2%'));
});

test("dark heatmaps put the strongest light on high values and retain data, gaps and original notes", () => {
  const spec={data:[{type:'heatmap',x:['一月','二月','三月'],y:['SP','RJ'],z:[[30,100,169],[32,80,null]],
    colorscale:[[0,'#E6F2EF'],[1,'#126a62']],colorbar:{title:'订单数'}}],
    layout:{annotations:[{text:'原备注',x:'一月',y:'SP',showarrow:false}]}};
  const before=JSON.stringify(spec),figure=charts.chartFigure(spec,true,580,360);
  assert.equal(figure.data[0].colorscale[0][1],'#132A2E');
  assert.equal(figure.data[0].colorscale.at(-1)[1],'#4CE0B3');
  const labels=figure.layout.annotations;
  assert.equal(labels.find(item=>item.text==='30').font.color,'#94A3B8');
  assert.equal(labels.find(item=>item.text==='169').font.color,'#091316');
  assert.equal(labels.length,6);
  assert.ok(labels.some(item=>item.text==='原备注'));
  assert.equal(JSON.stringify(figure.data[0].z),JSON.stringify(spec.data[0].z));
  assert.equal(figure.data.length,1);
  assert.equal(JSON.stringify(spec),before);
  assert.equal(charts.chartFigure(spec,false,580,360).data[0].colorscale[0][1],'#E6F2EF');
});

test("dark retention labels adapt to ratios instead of a hard-coded count threshold",()=>{
  const spec={data:[{type:'heatmap',x:[0,1,2],y:['2018-01'],z:[[1,.0055,null]],colorbar:{title:'retention_rate'}}]};
  const figure=charts.chartFigure(spec,true,580,360);
  assert.equal(figure.data[0].zmin,0);assert.equal(figure.data[0].zmax,1);
  assert.equal(figure.layout.annotations.find(item=>item.text==='100%').font.color,'#091316');
  assert.equal(figure.layout.annotations.find(item=>item.text==='0.55%').font.color,'#94A3B8');
  assert.equal(figure.layout.annotations.length,2);
  assert.equal(figure.data[0].z[0][2],null);
});

test("narrow time charts reduce overlapping labels without dropping observations or gaps", () => {
  const x=Array.from({length:12},(_,i)=>`2026-${String(i+1).padStart(2,'0')}-01`);
  const y=[10,20,null,15,0,-4,18,21,22,17,23,25];
  const source={data:[{type:'scatter',mode:'lines+markers',x,y}],layout:{yaxis:{title:'净销售额'}}};
  const before=JSON.stringify(source);
  const figure=charts.chartFigure(source,false,390,300);
  assert.ok(figure.layout.xaxis.tickvals.length<=6);
  assert.deepEqual(figure.data[0].x,x);
  assert.deepEqual(figure.data[0].y,y);
  assert.equal(JSON.stringify(source),before);
});

test("browser chart engine registers every supported trace on the same core", () => {
  const registered = [];
  const waterfall = { name: "waterfall" };
  const engine = load("lib/plotly.ts", {
    "plotly.js/lib/core": { register: (traces) => registered.push(...traces) },
    ...Object.fromEntries(['scatter', 'bar', 'histogram', 'heatmap', 'pie', 'funnel', 'treemap', 'sunburst', 'sankey','box','table','scattermap','choroplethmap'].map(name => [`plotly.js/lib/${name}`, { name }])),
    "plotly.js/lib/waterfall": waterfall,
  });
  assert.deepEqual(registered.map(trace => trace.name), ['scatter', 'bar', 'histogram', 'heatmap', 'pie', 'waterfall', 'funnel', 'treemap', 'sunburst', 'sankey','box','table','scattermap','choroplethmap']);
  assert.ok(engine.default.register);
});

test("SQL history pairs old generated queries with execution, preserving retries and validator failures", () => {
  const trace = [
    { node: "write_sql", sql: "SELECT broken" }, { node: "execute_sql", error: "missing column", seconds: .12, row_count: 0 },
    { node: "write_sql", sql: "SELECT actual" }, { node: "execute_sql", sql: "SELECT actual", status: "succeeded", row_count: 4 },
    { node: "validator", validation_failure: "missing values" }, { node: "write_sql", sql: "SELECT next" },
  ];
  const result = analysis.sqlExecutions({ evidence: { trace } });
  assert.equal(result.length, 3);
  assert.equal(result[0].sql, "SELECT broken");
  assert.equal(result[0].status, "failed");
  assert.equal(result[1].rowCount, 4);
  assert.equal(result[1].validation, "missing values");
  assert.equal(result[2].status, "generated");
  assert.equal(trace[0].status, undefined);
});

test("equal SQL executed twice remains two attempts and legacy SQL does not invent execution status", () => {
  assert.equal(analysis.sqlExecutions({ evidence: { trace: [
    { node: "execute_sql", sql: "SELECT 1", status: "failed" }, { node: "execute_sql", sql: "SELECT 1", status: "running" },
  ] } }).length, 2);
  assert.equal(analysis.sqlExecutions({ sql: "SELECT 1", row_count: 1 })[0].status, "recorded");
});

test("session totals include live usage, missing reports and older unmeasured analyses", () => {
  const messages = [
    { role: "assistant", metadata: { evidence: { usage: { calls: [{}], input_tokens: 10, output_tokens: 5, total_tokens: 15, reported_calls: 1, cost_cny: "0.0005" } } } },
    { role: "assistant", metadata: { sql: "SELECT 1", route: "sql" } },
  ];
  const total = analysis.sessionUsage(messages, { input_tokens: 20, output_tokens: 2, total_tokens: 22, missing_usage_calls: 1, reported_calls: 1, cost_cny: "0.0001" });
  assert.equal(total.total, 37);
  assert.equal(total.legacy, 1);
  assert.equal(total.missing, 1);
  assert.ok(Math.abs(total.cost - .0006) < 1e-10);
  assert.equal(analysis.sessionUsage([]).cost, null);
});

test("chart styling preserves data and axis meaning while applying the requested dark palette", () => {
  const original = { data: [{ type: "bar", x: ["广州", "深圳"], y: [2, 3], marker: { color: "#123456" } }], layout: { title: "城市销售额", xaxis: { title: "城市" }, yaxis: { title: { text: "销售额（元）" } } } };
  const before = JSON.stringify(original);
  const output = charts.chartFigure(original, true, 650, 400);
  assert.equal(charts.chartTitle(original), "城市销售额");
  assert.equal(output.layout.xaxis.title.text, "城市");
  assert.equal(output.layout.yaxis.title.text, "");
  assert.ok(output.layout.annotations.some(item=>item.text==='销售额（元）'));
  assert.equal(output.layout.xaxis.tickangle, 0);
  assert.equal(output.data[0].marker.color, "#10B981");
  assert.equal(output.layout.width, 650);
  assert.equal(JSON.stringify(original), before);
});

for (const reversed of [false,true]) test(`reset restores both axes and preserves ${reversed ? 'cohort order after Plotly mutates autorange' : 'ordinary axes'}`, async () => {
  const plotElement = {};
  const calls = [];
  const { ChartView } = load("components/chart-view.tsx", {
    react: { ...React, useState: (value) => [value, () => {}], useRef: () => ({ current: plotElement }), useMemo: (fn) => fn(), useEffect() {} },
    "next-themes": { useTheme: () => ({ resolvedTheme: "light" }) },
    "next/dynamic": () => () => null,
    "@/lib/chart": charts,
    "@/lib/plotly": { __esModule: true, default: { relayout: async (...args) => calls.push(args) } },
  });
  const elements = (element) => !element || typeof element !== "object" ? [] : [element, ...React.Children.toArray(element.props?.children).flatMap(elements)];
  const spec=reversed ? {data:[{type:'heatmap',x:[0,1],y:['2018-01'],z:[[1,.1]],colorbar:{title:'retention_rate'}}]} : {};
  const canvas = elements(ChartView({ spec })).find((element) => element.type?.name === "ChartCanvas");
  const tree=elements(canvas.type(canvas.props));
  if(reversed) tree.find(element=>element.props?.layout).props.layout.yaxis.autorange=true;
  const reset = tree.find((element) => element.props?.["aria-label"] === "重置图表缩放");
  await reset.props.onClick();
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], plotElement);
  assert.equal(calls[0][1]["xaxis.autorange"], true);
  assert.equal(reversed ? calls[0][1].yaxis?.autorange : calls[0][1]["yaxis.autorange"], reversed ? 'reversed' : true);
});

test('hierarchy reset returns to the root rather than changing unused axes',async()=>{
  const calls=[],plotElement={};
  const {ChartView}=load('components/chart-view.tsx',{
    react:{...React,useState:v=>[v,()=>{}],useRef:()=>({current:plotElement}),useMemo:fn=>fn(),useEffect(){}},
    'next-themes':{useTheme:()=>({resolvedTheme:'light'})},'next/dynamic':()=>()=>null,'@/lib/chart':charts,
    '@/lib/plotly':{__esModule:true,default:{restyle:async(...args)=>calls.push(args)}},
  });
  const elements=node=>!node||typeof node!=='object'?[]:[node,...React.Children.toArray(node.props?.children).flatMap(elements)];
  const canvas=elements(ChartView({spec:{data:[{type:'treemap',ids:['a'],parents:[''],labels:['A'],values:[10]}]}})).find(n=>n.type?.name==='ChartCanvas');
  await elements(canvas.type(canvas.props)).find(n=>n.props?.['aria-label']==='重置图表缩放').props.onClick();
  assert.equal(calls.length,1);assert.equal(calls[0][0],plotElement);assert.equal(calls[0][1].level,'');
});

test('native sankey hover without point.data does not crash the dark chart',()=>{
  const {ChartView}=load('components/chart-view.tsx',{
    react:{...React,useState:v=>[v,()=>{}],useRef:()=>({current:null}),useMemo:fn=>fn(),useEffect(){}},
    'next-themes':{useTheme:()=>({resolvedTheme:'dark'})},'next/dynamic':()=>()=>null,'@/lib/chart':charts,
  });
  const elements=node=>!node||typeof node!=='object'?[]:[node,...React.Children.toArray(node.props?.children).flatMap(elements)];
  const canvas=elements(ChartView({spec:{data:[{type:'sankey',node:{label:['A','B']},link:{source:[0],target:[1],value:[10]}}]}})).find(n=>n.type?.name==='ChartCanvas');
  const plot=elements(canvas.type(canvas.props)).find(n=>n.props?.onHover);
  assert.doesNotThrow(()=>plot.props.onHover({points:[{pointNumber:1,value:10,source:{label:'A'},target:{label:'B'}}]}));
  assert.equal(analysis.businessLabel('amount_cny'),'金额（CNY）');
});

test('sankey node positions reset for new geometry while retaining every link',()=>{
  const spec={data:[{type:'sankey',node:{label:['A','B'],x:[.01,.99],y:[.5,.5]},link:{source:[0],target:[1],value:[10]}}]};
  const wide=charts.chartFigure(spec,false,1200,600),narrow=charts.chartFigure(spec,false,338,460);
  assert.notEqual(wide.data[0].uirevision,narrow.data[0].uirevision);
  assert.equal(narrow.data[0].link.value[0],10);
});

test("workspace prioritizes the associated visualization and keeps SQL inspectable", () => {
  const { AnalysisWorkspace } = load("components/analysis-workspace.tsx", {
    "@/lib/analysis": analysis, "@/lib/chart": charts,
    "@/components/chart-view": { ChartView: () => React.createElement("div", null, "CHART_SENTINEL") },
    "@/components/sql-timeline": { SqlTimeline: () => React.createElement("section", null, "SQL_SENTINEL") },
  });
  const html = renderToStaticMarkup(React.createElement(AnalysisWorkspace, {
    messages: [{ role: "assistant", content: "结果", metadata: { sql: "SELECT 1", chart_spec: { data: [{ type: "bar", x: [1], y: [1] }] } } }],
    progress: null, selection: null, onSelect() {}, onJump() {},
  }));
  assert.ok(html.includes("SQL_SENTINEL") && html.includes("CHART_SENTINEL"));
  assert.ok(html.indexOf("CHART_SENTINEL") < html.indexOf("SQL_SENTINEL"));
});

test('report sources resolve the exact workflow path and never borrow another chart',()=>{
  const a={sql:'SELECT A',chart_spec:{data:[{type:'bar',y:[10]}]}},b={python_code:'result = df',rows:[{n:0}]};
  const metadata={workflow:{steps:[{result:a},{result:b}]},analysis_report:{sources:[
    {key:'S1',label:'金额',path:['workflow','steps',0,'result'],has_chart:true},
    {key:'S2',label:'核对',path:['workflow','steps',1,'result'],has_chart:false},
  ]}};
  const sources=analysis.reportSources(metadata);
  assert.equal(sources[0].metadata,a);assert.equal(sources[1].metadata,b);
  assert.equal(sources[1].metadata.chart_spec,undefined);
});

test('investigation report paths can be added and retain the precise nested source',()=>{
  const leaf={status:'succeeded',step_id:'same',chart_spec:{data:[{type:'bar',y:[200]}]}};
  const path=['workflow','steps',1,'result','investigation','steps',0];
  const metadata={workflow:{steps:[{id:'first',status:'failed',result:{}},{id:'second',status:'succeeded',result:{investigation:{steps:[leaf]}}}]},
    analysis_report:{sources:[{key:'S1',label:'调查',path}]}};
  const source=analysis.reportSources(metadata)[0];
  assert.equal(source.metadata,leaf);assert.equal(source.canAdd,true);
  assert.deepEqual(Array.from(source.sourcePath),path);
});

test("workspace never borrows an earlier chart or SQL for a new reply or pending task", () => {
  const {AnalysisWorkspace}=load('components/analysis-workspace.tsx',{
    '@/lib/analysis':analysis,'@/lib/chart':charts,
    '@/components/chart-view':{ChartView:()=>React.createElement('div',null,'OLD_CHART')},
    '@/components/sql-timeline':{SqlTimeline:({metadata})=>React.createElement('div',null,metadata.sql || 'NO_SQL')},
  });
  const messages=[{role:'user',content:'旧分析',metadata:{}},
    {role:'assistant',content:'旧结果',metadata:{sql:'SELECT old',chart_spec:{data:[{type:'bar'}]}}},
    {role:'user',content:'介绍这些表',metadata:{}},{role:'assistant',content:'这是数据介绍',metadata:{route:'chat'}}];
  const render=(props={})=>renderToStaticMarkup(React.createElement(AnalysisWorkspace,{messages,progress:null,selection:null,onSelect(){},onJump(){},...props}));
  for(const props of [{},{selection:3},{selection:999},{pending:true},{progress:{trace:[]}},
    {messages:messages.slice(0,3)}]) {
    const html=render(props);
    assert.ok(!html.includes('OLD_CHART') && !html.includes('SELECT old'));
    assert.ok(html.includes('NO_SQL'));
  }
  const history=render({selection:1});
  assert.ok(history.includes('OLD_CHART') && history.includes('SELECT old'));
});

test("task result recovery preserves the answer, SQL and usage without appending it twice", () => {
  const usage = { input_tokens: 1200, output_tokens: 380, total_tokens: 1580, reported_calls: 1, cost_cny: "0.00272", calls: [{}] };
  const task = { task_id: "recovered", status: "succeeded", trace: [], model_calls: 1, max_model_calls: 30, usage,
    result: { question: "销售额", answer: "123", sql: "SELECT 123", evidence: {} } };
  const previous = [{ role: "user", content: "销售额", metadata: {} }];
  const recovered = analysis.appendTaskResult(previous, task);
  assert.equal(recovered.length, 2);
  assert.equal(recovered[1].content, "123");
  assert.equal(recovered[1].metadata.sql, "SELECT 123");
  assert.equal(analysis.sessionUsage(recovered).total, 1580);
  assert.equal(analysis.sessionUsage(recovered).cost, .00272);
  assert.equal(analysis.appendTaskResult(recovered, task).length, 2);
  assert.equal(previous.length, 1);
});

test("failed and cancelled tasks retain usage when history is unavailable", () => {
  for (const status of ["failed", "cancelled"]) {
    const [message] = analysis.appendTaskResult([], { task_id: status, status, trace: [], error: "执行失败", model_calls: 1, max_model_calls: 30,
      usage: { total_tokens: 100, reported_calls: 1, cost_cny: "0.001", calls: [{}] } });
    assert.ok(message.content.length > 0);
    assert.equal(message.metadata.evidence.usage.total_tokens, 100);
  }
});

test('successful history reads do not discard a failed task and its computed answer',()=>{
  const task={task_id:'save-failed',status:'failed',error:'结果保存失败',trace:[],model_calls:0,max_model_calls:30,
    result:{question:'本轮问题',answer:'已计算的测试答案',evidence:{}}};
  const history=[{role:'user',content:'上传文件',metadata:{}}];
  const restored=analysis.reconcileTaskHistory(history,task);
  assert.equal(restored.saved,false);
  assert.ok(restored.messages.at(-1).content.includes('结果保存失败'));
  assert.ok(restored.messages.at(-1).content.includes('已计算的测试答案'));
  const saved=[...history,{role:'assistant',content:'已保存答案',metadata:{evidence:{task_id:task.task_id}}}];
  const synced=analysis.reconcileTaskHistory(saved,task);
  assert.equal(synced.saved,true);
  assert.equal(synced.messages.length,2);
});

for (const fails of [false, true]) test(`PNG ${fails ? "failure" : "success"} restores the displayed layout and zoom`, async () => {
  const plotElement = {};
  const exports = [];
  const api = { relayout: async () => { throw new Error('Visible chart must not repaint during export'); }, downloadImage: async (figure) => { exports.push(figure); if (fails) throw new Error("export failed"); } };
  const { ChartView } = load("components/chart-view.tsx", {
    react: { ...React, useState: (value) => [value, () => {}], useRef: () => ({ current: plotElement }), useMemo: (fn) => fn(), useEffect() {} },
    "next-themes": { useTheme: () => ({ resolvedTheme: "dark" }) }, "next/dynamic": () => () => null,
    "@/lib/chart": charts, "@/lib/plotly": { __esModule: true, default: api },
    "@/lib/chart-effects": {finishChart(){},downloadChartPng:api.downloadImage},
  });
  const elements = (element) => !element || typeof element !== "object" ? [] : [element, ...React.Children.toArray(element.props?.children).flatMap(elements)];
  const canvas = elements(ChartView({ spec: {layout:{meta:{unit_panels:4}}} })).find((element) => element.type?.name === "ChartCanvas");
  const tree = canvas.type(canvas.props);
  plotElement.layout = elements(tree).find((element) => element.props?.layout).props.layout;
  plotElement.layout.xaxis.range = [1, 3]; plotElement.layout.yaxis.range = [200, 300];
  const before = JSON.stringify(plotElement.layout);
  await elements(tree).find((element) => element.props?.["aria-label"] === "下载高清 PNG").props.onClick();
  assert.equal(exports.length, 1);
  assert.notEqual(exports[0], plotElement);
  assert.equal(exports[0].layout.height,1160);
  assert.equal(JSON.stringify(plotElement.layout), before);
});


test('successful empty tables keep their schema in the workspace',()=>{
  const {AnalysisWorkspace}=load('components/analysis-workspace.tsx',{
    '@/lib/analysis':analysis,'@/lib/chart':charts,
    '@/components/chart-view':{ChartView:()=>null},'@/components/sql-timeline':{SqlTimeline:()=>null},
    '@/components/data-panel':{ResultTable:({rows,columns,totalRows})=>React.createElement('div',null,`${columns.join(',')} / ${totalRows} / ${rows.length}`)},
  });
  const render=metadata=>renderToStaticMarkup(React.createElement(AnalysisWorkspace,{
    messages:[{role:'assistant',content:'无匹配记录',metadata}],progress:null,selection:null,onSelect(){},onJump(){},
  }));
  assert.ok(render({sql:'SELECT amount WHERE false',rows:[],columns:['amount'],row_count:0}).includes('amount / 0 / 0'));
  for(const metadata of [{rows:[],columns:[]},{rows:[],columns:['amount'],error:'failed'},{}])
    assert.ok(!render(metadata).includes('实际结果数据'));
});
