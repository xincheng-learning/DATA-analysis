import type { Annotations, Data, Layout } from "plotly.js";
import { businessLabel, record, records, type RecordValue } from "./analysis";
import {chartPalettes,type ChartAppearance} from './chart-appearance';
import {packedBubbles} from './packed-bubbles';

export const luminousPalette = ['#10B981', '#0EA5E9', '#F59E0B', '#8B5CF6', '#F43F5E'];
export const chartMono = "'JetBrains Mono', 'Geist Mono', 'Inter', Consolas, monospace";
export const chartRgba = (hex: string, alpha: number) => `rgba(${[1,3,5].map(index=>parseInt(hex.slice(index,index+2),16)).join(',')},${alpha})`;
export const axisTitle = (value: unknown) => {
  const title = record(value).title;
  return typeof title === 'string' ? title : String(record(title).text || '');
};
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const categoryValues = (trace: RecordValue): unknown[] => Array.isArray(trace.orientation === 'h' ? trace.y : trace.x) ? (trace.orientation === 'h' ? trace.y : trace.x) as unknown[] : [];
const measureValues = (trace: RecordValue): unknown[] => Array.isArray(trace.orientation === 'h' ? trace.x : trace.y) ? (trace.orientation === 'h' ? trace.x : trace.y) as unknown[] : [];
const isTrend = (trace: RecordValue) => trace.type === 'scatter' && String(trace.mode).includes('lines') && !trace.stackgroup;
const isSeries = (trace: RecordValue) => trace.type === 'bar' || isTrend(trace);

type ValueScale = 'number' | 'ratio' | 'percent';
const valueScale = (trace: RecordValue, axis: RecordValue): ValueScale => {
  const scale = record(trace.meta).value_scale;
  return scale === 'ratio' || scale === 'percent' ? scale : scale === 'number' ? 'number' : String(axis.tickformat || '').includes('%') ? 'ratio' : 'number';
};

export function chartNumber(value: number, title = '', compact = false, scale: ValueScale = 'number'): string {
  const currency = /BRL|R\$/i.test(title) ? 'R$ ' : /CNY|RMB|人民币|元|¥/i.test(title) ? '¥ ' : /USD|\$/i.test(title) ? '$ ' : /EUR|€/i.test(title) ? '€ ' : '';
  // 用户2026-11-15明确：微小非零在展示层按原精度四舍五入成 0 可接受，不调精度实现。
  // 底层数值仍保留（见 chartTooltip/chartAnalysis 的原始值），导出与悬停不得改写原数。
  if (scale !== 'number') return `${(scale === 'ratio' ? value * 100 : value).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}%`;
  const divisor = compact && Math.abs(value) >= 1e6 ? 1e6 : compact && Math.abs(value) >= 1e3 ? 1e3 : 1;
  return currency + (value / divisor).toLocaleString('en-US', { maximumFractionDigits: divisor > 1 ? 1 : 2 }) + (divisor === 1e6 ? 'm' : divisor === 1e3 ? 'k' : '');
}

/** Format a thinned date tick without collapsing distinct days/hours into the same month label. */
export function formatChartDateTick(value: unknown, spanSamples: unknown[] = []): string {
  const raw = String(value);
  const hasTime = /T\d{2}:/.test(raw) || /\d{2}:\d{2}/.test(raw);
  const day = raw.match(/^(\d{4})-(\d{2})-(\d{2})/);
  const month = raw.match(/^(\d{4})-(\d{2})$/);
  const samples = spanSamples.map(String);
  const multiYear = samples.some((item) => item.slice(0, 4) !== samples[0]?.slice(0, 4));
  if (hasTime && day) {
    const time = raw.match(/T?(\d{2}):(\d{2})/);
    return multiYear ? `${day[1]}-${day[2]}-${day[3]} ${time?.[1] || '00'}:${time?.[2] || '00'}` : `${day[2]}-${day[3]} ${time?.[1] || '00'}:${time?.[2] || '00'}`;
  }
  if (day) {
    const distinctDays = new Set(samples.map((item) => item.slice(0, 10))).size > 1 || samples.length <= 1;
    if (distinctDays) return multiYear ? `${day[1]}-${day[2]}-${day[3]}` : `${day[2]}-${day[3]}`;
    return `${day[1]}-${day[2]}`;
  }
  if (month) return `${month[1]}-${month[2]}`;
  return raw.slice(0, 16);
}

/** Presentation statistics only: never fetches data or mutates a saved figure. */
export function chartAnalysis(spec: RecordValue) {
  const traces = records(spec.data).filter(trace => trace.visible !== false && trace.visible !== 'legendonly');
  const first = traces[0] || {}, layout = record(spec.layout);
  const horizontal = first.orientation === 'h', valueAxis = horizontal ? 'xaxis' : 'yaxis';
  const title = axisTitle(layout[valueAxis]);
  const scale = valueScale(first, record(layout[valueAxis]));
  const format = (value: number, compact = false) => chartNumber(value, title, compact, scale);
  const totals: { category: unknown; value: number; complete: boolean }[] = [];
  const kpis: { label: string; value: string; detail?: string }[] = [];
  const empty = { totals, kpis, average: null as number | null, scope: '', additive: false, horizontal, title, scale };
  // Different axes/units, normalization and explicit bases cannot share an aggregate.
  if (!traces.length || !traces.every(trace => isSeries(trace) &&
    (trace.xaxis || 'x') === (first.xaxis || 'x') && (trace.yaxis || 'y') === (first.yaxis || 'y') &&
    (trace.orientation || 'v') === (first.orientation || 'v') && trace.base == null && !trace.transforms &&
    new Set(categoryValues(trace)).size === categoryValues(trace).length) || layout.barnorm ||
    record(layout[valueAxis]).type === 'log' || first.xaxis && first.xaxis !== 'x' || first.yaxis && first.yaxis !== 'y') return empty;
  const bars = traces.every(trace => trace.type === 'bar');
  const comparable = bars && (traces.length === 1 || layout.barmode === 'stack' || layout.barmode === 'relative');
  const metric = [title, ...traces.map(trace=>`${trace.name || ''} ${record(trace.meta).metric_column || ''}`)].join(' ');
  const nonadditive = /客单|平均|均值|单价|中位|去重|独立|客户数|用户数|人数|最大|最小|峰值|率|占比|%|\b(avg|average|mean|median|aov|distinct|unique|rate|ratio|share)\b|customer_count/i.test(metric.replaceAll('_',' '));
  const summable = !nonadditive && scale === 'number' && /amount|revenue|sales|freight|quantity|金额|销售额|运费|销量|件数|订单数|order.count/i.test(metric);
  const additive = comparable && summable && traces.every(trace=>valueScale(trace,record(layout[valueAxis])) === 'number');
  const selected = additive ? traces : [first];
  const categories = Array.from(new Set(selected.flatMap(categoryValues)));
  let missing = false;
  for (const category of categories) {
    const values = selected.map(trace => measureValues(trace)[categoryValues(trace).indexOf(category)]);
    const known = values.filter(finite);
    if (known.length !== values.length) missing = true;
    if (known.length) totals.push({ category, value: known.reduce((sum, value) => sum + value, 0), complete: known.length === values.length });
  }
  if (!totals.length) return empty;
  const average = summable && !missing ? totals.reduce((sum, point) => sum + point.value, 0) / totals.length : null;
  const peak = totals.reduce((best, point) => point.value > best.value ? point : best);
  const total = totals.reduce((sum, point) => sum + point.value, 0);
  const date = /^\d{4}-\d{2}/.test(String(peak.category));
  const monthly = totals.every(point => /^\d{4}-\d{2}(?:$|-01(?:[ T]00:00:00(?:\.0+)?Z?)?$)/.test(String(point.category)));
  const scope = `仅图示数据 · ${totals.length} ${monthly ? '个月' : '个类别'}${!additive && traces.length > 1 ? ` · ${businessLabel(String(first.name || '首序列'))}` : ''}${missing ? ' · 含缺失值，按已知值计算' : ''}`;
  if (additive) {
    kpis.push({ label: missing?'图示已知合计':'图示总计', value: format(total, true), detail: format(total)+(missing?' · 存在缺失，非完整总额':'') });
    kpis.push({ label: missing?'已知值峰值':monthly ? '峰值月份' : date ? '峰值日期' : '峰值类别', value: String(peak.category).replace(/T00:00:00.*$/, ''), detail: format(peak.value, true) });
    const nonnegative = selected.every(trace => measureValues(trace).every(value => !finite(value) || value >= 0));
    if (selected.length > 1 && nonnegative && total > 0 && !missing) {
      const contributions = selected.map(trace => ({ name: businessLabel(String(trace.name || '序列')), value: measureValues(trace).filter(finite).reduce((sum, value) => sum + value, 0) }));
      const leader = contributions.reduce((best, item) => item.value > best.value ? item : best);
      kpis.push({ label: '核心贡献', value: `${leader.name} · ${(leader.value / total * 100).toFixed(1)}%` });
    } else if (average != null) kpis.push({ label: monthly ? '月均值' : '类别均值', value: format(average, true) });
  } else {
    kpis.push({ label: date ? '最新观测' : '末项展示值', value: format(totals.at(-1)!.value, true), detail: String(totals.at(-1)!.category) });
    kpis.push({ label: '峰值', value: format(peak.value, true), detail: String(peak.category) });
    if (average != null) kpis.push({ label: '观测均值', value: format(average, true) });
  }
  return { totals, kpis, average, scope, additive, horizontal, title, scale };
}

export function chartTooltip(spec: RecordValue, category: unknown, displayed:RecordValue=spec) {
  const analysis = chartAnalysis(spec), layout = record(spec.layout);
  const rows = records(spec.data).flatMap((trace, index) => {
    if (!isSeries(trace) || trace.visible === false || trace.visible === 'legendonly') return [];
    const at = categoryValues(trace).indexOf(category), value = measureValues(trace)[at];
    const axisName = trace.orientation === 'h' ? String(trace.xaxis || 'x') : String(trace.yaxis || 'y');
    const title = axisTitle(layout[axisName.replace(/^([xy])/, '$1axis')]);
    const drawn=records(displayed.data)[index]||trace,marker=record(drawn.marker);
    const color=isTrend(trace)?record(drawn.line).color:Array.isArray(marker.color)?marker.color[at]:marker.color;
    return [{ name: businessLabel(String(trace.name || '数值')), color: String(color||luminousPalette[index % luminousPalette.length]),
      value: finite(value) ? value : null, formatted: finite(value) ? chartNumber(value, title, false, valueScale(trace,record(layout[axisName.replace(/^([xy])/, '$1axis')]))) : '—', share: null as string | null }];
  });
  const point = analysis.totals.find(point => point.category === category);
  if (analysis.additive && point && point.value > 0 && point.complete && rows.every(row => row.value != null && row.value >= 0)) {
    rows.forEach(row => { row.share = `${(row.value! / point.value * 100).toFixed(1)}%`; });
  }
  return { heading: String(category), rows, total: analysis.additive && point ? chartNumber(point.value, analysis.title, false, analysis.scale) : null,
    totalLabel: /^\d{4}-\d{2}$/.test(String(category)) ? '当月总计' : '当前合计', incomplete: point?.complete === false };
}

const escapeChartText=(value:unknown)=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
function chartLabel(value:unknown,width=12,maxLines=2){
  const chars=Array.from(String(value)),limit=width*maxLines;
  const shown=chars.length>limit?[...chars.slice(0,limit-1),'…']:chars;
  return Array.from({length:Math.ceil(shown.length/width)},(_,index)=>escapeChartText(shown.slice(index*width,(index+1)*width).join(''))).join('<br>');
}

export function chartReadability(spec:RecordValue){
  const traces=records(spec.data).filter(trace=>trace.visible!==false&&trace.visible!=='legendonly');
  const warnings:string[]=[];
  const series=traces.filter(trace=>trace.type==='bar'||trace.type==='scatter'&&String(trace.mode).includes('lines'));
  const categoryCount=Math.max(0,...series.filter(trace=>trace.type==='bar').map(trace=>categoryValues(trace).length));
  if(series.some(trace=>{
    const peers=series.filter(other=>(other.xaxis||'x')===(trace.xaxis||'x')&&(other.yaxis||'y')===(trace.yaxis||'y'));
    const union=new Set(peers.flatMap(categoryValues));
    return measureValues(trace).some(value=>value==null)||categoryValues(trace).length<union.size;
  }))warnings.push('系列含缺失或未观测类别；只展示已知部分，不按零补齐，不代表完整总额，连线不证明缺失期间已有观测。');
  if(categoryCount>30)warnings.push(`共有${categoryCount}个类别，未自动删减；刻度名称可能抽稀，完整名称可悬停。汇报前建议明确前N名或先按业务维度汇总。`);
  let flowHeight=0;
  for(const trace of traces.filter(trace=>trace.type==='sankey')){
    const node=record(trace.node),labels=Array.isArray(node.label)?node.label:[],positions=Array.isArray(node.x)?node.x:[];
    const layers=new Map<unknown,number>();positions.forEach(x=>layers.set(x,(layers.get(x)||0)+1));
    const perLayer=layers.size?Math.max(...Array.from(layers.values())):Math.ceil(labels.length/2);
    flowHeight=Math.max(flowHeight,perLayer*34+240);
    if(labels.length>40)warnings.push(`流向包含${labels.length}个节点，权重未删减；长名称悬停可查，汇报前建议按层筛选或汇总。`);
  }
  const horizontal=traces.some(trace=>trace.type==='bar'&&trace.orientation==='h');
  return {warnings,height:Math.min(1800,Math.max(326,horizontal?categoryCount*30+120:326,flowHeight))};
}

export function chartExportFigure(spec:RecordValue,dark:boolean){
  const review=chartReadability(spec),panels=Number(record(record(spec.layout).meta).unit_panels)||1;
  const gap=records(spec.data).some(trace=>trace.stackgroup)?'未知期间留空；堆叠只展示各系列均已知的期间。':'';
  const note=[chartAnalysis(spec).scope,String(record(record(spec.layout).meta).chart_note||''),...review.warnings,gap].filter(Boolean).join(' ');
  const lines=Array.from({length:Math.ceil(Array.from(note).length/78)},(_,index)=>escapeChartText(Array.from(note).slice(index*78,(index+1)*78).join('')));
  const margin={l:90,r:70,t:110,b:Math.max(90,lines.length*18+100)};
  const figure=chartFigure(spec,dark,1400,Math.max(850,panels*260+120,review.height),margin);
  figure.layout={...figure.layout,title:{text:escapeChartText(chartTitle(spec)),font:{size:21},y:.98,yanchor:'top'},
    annotations:[...(figure.layout.annotations||[]).filter(annotation=>annotation.yshift!==-62||!String(annotation.text).startsWith('未知期间留空')),
      ...(lines.length?[{text:lines.join('<br>'),xref:'paper' as const,yref:'paper' as const,x:0,y:0,yshift:-78,xanchor:'left' as const,yanchor:'top' as const,showarrow:false,font:{size:12}}]:[])]};
  return figure;
}

const darkHeatmapColors = ['#132A2E', '#17464A', '#1B6867', '#209083', '#28B89E', '#4CE0B3'];
const rgb = (hex: string) => [1, 3, 5].map(index => parseInt(hex.slice(index, index + 2), 16));

function heatmapLabelColor(position: number): string {
  const step = Math.max(0, Math.min(1, position)) * (darkHeatmapColors.length - 1);
  const lower = Math.floor(step), upper = Math.min(lower + 1, darkHeatmapColors.length - 1);
  const start = rgb(darkHeatmapColors[lower]), end = rgb(darkHeatmapColors[upper]);
  const background = start.map((channel, index) => channel + (end[index] - channel) * (step - lower));
  return readableText(background,true);
}

function readableText(background:number[],muted=false):string {
  const luminance = (channels: number[]) => channels.map(channel => {
    const value = channel / 255;
    return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
  }).reduce((total, value, index) => total + value * [.2126, .7152, .0722][index], 0);
  const light = luminance(background);
  const contrast = (color: string) => {
    const text = luminance(rgb(color));
    return (Math.max(light, text) + .05) / (Math.min(light, text) + .05);
  };
  if (muted && contrast('#94A3B8') >= 4.5) return '#94A3B8';
  return contrast('#091316') >= contrast('#F1F5F9') ? '#091316' : '#F1F5F9';
}

export function chartTitle(spec: RecordValue): string {
  const title = record(spec.layout).title;
  return typeof title === "string" ? title : String(record(title).text || "分析图表");
}

export function segmentStackedAreas(traces:RecordValue[]):RecordValue[] {
  const groups=new Map<string,number[]>(),pieces=new Map<number,RecordValue[]>();
  traces.forEach((trace,index)=>{if(trace.stackgroup){const key=String(trace.stackgroup);groups.set(key,[...(groups.get(key)||[]),index]);}});
  for(const [key,indices] of Array.from(groups)){
    const maps=indices.map(index=>new Map((traces[index].x as unknown[]).map((x,i)=>[x,{index:i,value:(traces[index].y as unknown[])[i]}])));
    const xs=Array.from(new Set(indices.flatMap(index=>traces[index].x as unknown[])));
    if(xs.every(x=>typeof x==='number'))xs.sort((a,b)=>Number(a)-Number(b));
    else if(xs.every(x=>typeof x==='string'&&/^\d{4}-\d{2}/.test(x)))xs.sort((a,b)=>String(a).localeCompare(String(b)));
    const active=indices.map((index,i)=>traces[index].visible!==false&&traces[index].visible!=='legendonly'?i:-1).filter(i=>i>=0);
    if(!active.length||xs.every(x=>active.every(i=>finite(maps[i].get(x)?.value))))continue;
    const segments:unknown[][]=[];let current:unknown[]=[];
    for(const x of xs){
      if(active.every(i=>finite(maps[i].get(x)?.value)))current.push(x);
      else if(current.length){segments.push(current);current=[];}
    }
    if(current.length)segments.push(current);
    indices.forEach((index,i)=>{
      const trace=traces[index];
      pieces.set(index,(segments.length?segments:[[]]).map((segment,n)=>{
        const selected=segment.map(x=>maps[i].get(x)?.index??-1);
        return {...trace,x:segment,y:segment.map(x=>maps[i].get(x)?.value??null),cliponaxis:false,stackgroup:`${key}:segment-${n}`,
          legendgroup:`${key}:source-${index}`,showlegend:n===0&&trace.showlegend!==false,
          ...(Array.isArray(trace.customdata)?{customdata:selected.map(j=>(trace.customdata as unknown[])[j])}:{}),
          meta:{...record(trace.meta),area_gap_split:true,source_trace_index:index,source_indices:selected}};
      }));
    });
  }
  return traces.flatMap((trace,index)=>pieces.get(index)||[trace]);
}

export function chartFigure(spec: RecordValue, dark: boolean, width: number, height: number, marginOverride?:{l:number;r:number;t:number;b:number}) {
  const appearance=(spec.presentation||{}) as ChartAppearance;
  const customPalette=appearance.palette?chartPalettes[appearance.palette]:undefined;
  const palette = customPalette || (dark ? luminousPalette : ["#2d7668", "#627f9b", "#ae8748", "#a96b65"]);
  const ink = dark ? "#94A3B8" : "#43574d";
  const surface = dark ? "#141B26" : "#ffffff";
  const chartText = appearance.font_size || (width >= 1000 ? 14 : 12);
  const original = record(spec.layout);
  const independentUnits = record(original.meta).independent_units === true;
  const traces = segmentStackedAreas(records(spec.data));
  const margin={...(marginOverride||(traces.some(trace=>['treemap','sunburst','sankey','table','scattermap','choroplethmap'].includes(String(trace.type))||record(trace.meta).family==='packed_bubble')
    ?{l:18,r:18,t:20,b:20}:{l:58,r:record(original.meta).dual_axis?78:traces.some(trace=>trace.type==='heatmap'||record(trace.meta).color_column)?100:24,t:48,b:52}))};
  if(traces.some(trace=>trace.type==='bar'&&trace.orientation==='h'&&categoryValues(trace).some(label=>String(label).length>14)))margin.l=Math.max(margin.l,Math.min(240,Math.max(120,width*.35)));
  const analysis = chartAnalysis(spec);
  const cellLabels: Partial<Annotations>[] = [];
  const axis = (value: unknown, grid: boolean) => {
    const source = record(value);
    const rawTitle = typeof source.title === 'string' ? source.title : String(record(source.title).text || '');
    return { automargin: true, showgrid: grid,
      ...source,...(Array.isArray(source.range)?{range:[...source.range]}:{}), gridcolor: dark ? "rgba(255,255,255,0.05)" : "#eef1ef", griddash: 'dash', gridwidth: 1, nticks: 5,
      showline: false, zeroline: false, tickangle: 0, ticks: '', tickfont: { size: appearance.font_size || (dark ? 11 : chartText), color: dark ? '#64748B' : ink, family: chartMono },
      title: { ...record(source.title), text: businessLabel(rawTitle), standoff: 16, font: { size: chartText, color: ink } } };
  };
  const data = traces.map((trace, renderIndex) => {
    const index=Number(record(trace.meta).source_trace_index??renderIndex);
    if(record(trace.meta).family==='packed_bubble'){const packed=packedBubbles(trace,width-margin.l-margin.r,height-margin.t-margin.b);return {...packed,marker:{...record(packed.marker),color:palette[0]},textfont:{color:'#ffffff',size:chartText}};}
    const originalMarker = record(trace.marker);
    const numericColor=!!record(trace.meta).color_column;
    const colors=Array.isArray(originalMarker.colors)?Array.from(new Set(originalMarker.colors)):[];
    const marker = customPalette ? {...originalMarker,...(!numericColor&&!['treemap','sunburst','pie'].includes(String(trace.type))?{color:palette[index%palette.length]}:{}),
      ...(numericColor?{colorscale:[[0,palette.at(-1)],[1,palette[0]]]}:{}),
      ...(Array.isArray(originalMarker.colors)?{colors:originalMarker.colors.map(color=>palette[colors.indexOf(color)%palette.length])}:{})} : originalMarker;
    const name = typeof trace.name === "string" ? businessLabel(trace.name) : trace.name;
    if(['gantt','bullet'].includes(String(record(trace.meta).family)))return {...trace,name,marker:{...marker,color:palette[index%palette.length],line:{...record(marker.line),color:palette[index%palette.length]}},hoverinfo:undefined,hovertemplate:trace.hovertemplate||(trace.type==='bar'?'%{y}<br>实际：%{x}<extra></extra>':'%{y}<br>目标：%{x}<extra></extra>')};
    if(trace.type==='scattermap')return {...trace,marker:{...marker,...(!numericColor?{color:(trace.text as unknown[]).map((_,i)=>palette[i%palette.length])}:{})}};
    if(trace.type==='choroplethmap')return {...trace,colorscale:[[0,dark?'#293e52':'#e6eff5'],[1,palette[0]]],colorbar:{...record(trace.colorbar),tickfont:{color:ink},title:{text:businessLabel(axisTitle({title:record(trace.colorbar).title})),font:{color:ink,size:chartText}}}};
    if(trace.type==='table')return {...trace,header:{...record(trace.header),values:(record(trace.header).values as string[]).map(businessLabel),height:32,fill:{color:dark?'#203246':'#e9f0ee'},font:{color:dark?'#E2E8F0':ink,size:chartText},align:'left'},
      cells:{...record(trace.cells),values:(record(trace.cells).values as unknown[][]).map(column=>column.map(value=>value==null?'空值':value)),height:28,fill:{color:surface},font:{color:dark?'#E2E8F0':ink,size:chartText},align:'left'},domain:{x:[0,1],y:[0,1]}};
    if(trace.type==='box')return {...trace,line:{color:dark?palette[3]:palette[0],width:2.2},fillcolor:chartRgba(palette[1],dark?.3:.22),
      marker:{...originalMarker,color:dark?'#e9ad8b':'#b75a41',size:6},hoverlabel:{font:{size:13}}};
    if(numericColor)marker.colorbar={...record(marker.colorbar),title:{text:businessLabel(String(record(trace.meta).color_column)),font:{color:ink,size:chartText}},tickfont:{color:ink,size:chartText}};
    if(trace.type==='treemap'||trace.type==='sunburst')return {...trace,root:{color:surface},
      marker:{...marker,line:{color:surface,width:3}},textfont:{size:appearance.font_size||14,
        color:Array.isArray(marker.colors)?marker.colors.map((color,i)=>readableText(rgb((trace.parents as unknown[])?.[i]===''?surface:String(color)))):ink},hoverlabel:{font:{size:13}}};
    if(trace.type==='sankey'){
      const node=record(trace.node),link=record(trace.link),colors=customPalette?(node.label as unknown[]).map((_,i)=>palette[i%palette.length]):node.color;
      const labels=node.label as unknown[],template=(value:unknown,index:number,fallback:string)=>String(Array.isArray(value)?value[index]:value||fallback);
      return {...trace,uirevision:`sankey-${width}-${height}`,textfont:{size:appearance.font_size||12,color:ink},
        node:{...node,label:labels.map(label=>chartLabel(label,12)),hovertemplate:labels.map((label,i)=>template(node.hovertemplate,i,'%{label}<br>%{value}<extra></extra>').replaceAll('%{label}',escapeChartText(label))),color:colors,line:{color:surface,width:1}},link:{...link,
          hovertemplate:(link.source as number[]).map((source,i)=>template(link.hovertemplate,i,'%{source.label} → %{target.label}<br>%{value}<extra></extra>').replaceAll('%{source.label}',escapeChartText(labels[source])).replaceAll('%{target.label}',escapeChartText(labels[(link.target as number[])[i]]))),
          ...(customPalette?{color:(link.source as number[]).map(source=>chartRgba((colors as string[])[source],.26))}:{})}};
    }
    if (trace.type === "heatmap") {
      const colorbar = record(trace.colorbar), title = colorbar.title;
      const raw = typeof title === "string" ? title : String(record(title).text || "数值");
      const cells = trace.z as (number | null)[][];
      const values = cells.flat().filter((value): value is number => typeof value === 'number');
      // Old saved retention charts use the established 0–1 convention.
      const legacyRatio = !('value_scale' in record(trace.meta)) && /retention_rate|repeat_rate|conversion_rate/.test(raw);
      const scale = legacyRatio ? 'ratio' : valueScale(trace, {});
      const percentage = scale !== 'number';
      const sequential = dark && values.every(value => value >= 0);
      const minimum = percentage ? 0 : typeof trace.zmin === 'number' ? trace.zmin : values.length ? Math.min(...values) : 0;
      const maximum = percentage ? scale === 'ratio' ? 1 : 100 : typeof trace.zmax === 'number' ? trace.zmax : values.length ? Math.max(...values) : 1;
      const labels = cells.map(row => row.map(value => value==null ? '' : percentage ? chartNumber(value, '', false, scale) : value.toLocaleString('zh-CN',{maximumFractionDigits:1})));
      if (sequential) cells.forEach((row, y) => row.forEach((value, x) => {
        if (value == null) return;
        cellLabels.push({ x: (trace.x as (string | number)[])[x], y: (trace.y as (string | number)[])[y],
          xref: (trace.xaxis || 'x') as Annotations['xref'], yref: (trace.yaxis || 'y') as Annotations['yref'],
          text: labels[y][x], showarrow: false, captureevents: false,
          font: { family: 'Geist Mono, Consolas, monospace', size: 12, weight: 600,
            color: heatmapLabelColor(maximum === minimum ? .5 : (value-minimum)/(maximum-minimum)) } });
      }));
      return { ...trace, name, xgap: 3, ygap: 3, hoverongaps: false,
        text: labels, texttemplate: sequential ? '' : '%{text}', textfont: { size: chartText, color: 'auto' },
        ...(customPalette ? {colorscale:[[0,dark?surface:'#f0f5fa'],[1,palette[0]]],reversescale:false} : sequential ? { colorscale: darkHeatmapColors.map((color,index)=>[index/(darkHeatmapColors.length-1),color]), reversescale: false } : {}),
        ...(percentage ? { zmin: 0, zmax: maximum } : {}),
        colorbar: { ...colorbar, title: { text: businessLabel(raw), side: "right", font: { size: 11 } },
          thickness: dark ? 10 : 8, len: .85, outlinewidth: 0, tickfont: { size: 11, color: dark ? '#64748B' : ink }, ...(scale === 'ratio' ? { tickformat: ".0%" } : scale === 'percent' ? { ticksuffix:'%' } : {}) },
        hovertemplate: `%{y} · %{x}<br>${businessLabel(raw)}：%{z${scale === 'ratio' ? ":.1%" : ":,.2f"}}${scale === 'percent' ? '%' : ''}<extra></extra>` };
    }
    if (trace.type==='funnel') return { ...trace, y: Array.isArray(trace.y) ? trace.y.map(value=>businessLabel(String(value))) : trace.y,
      marker:{color:palette[0],line:{color:surface,width:2}},textfont:{size:13,color:dark?'#162b22':'#ffffff'},texttemplate:'%{value:,}<br>%{percentInitial:.2%}',
      hovertemplate:'%{y}<br>到达数：%{x:,}<br>相对首阶段：%{percentInitial:.2%}<extra></extra>' };
    const lineChart = isTrend(trace);
    const hoverFormat = (values:unknown) => Array.isArray(values) && values.every(value=>value==null || Number.isInteger(value)) ? ':,d' : ':,.2f';
    const horizontal = trace.orientation === 'h';
    const hovertemplate = trace.hovertemplate || (horizontal ? `%{y}<br>%{x${hoverFormat(trace.x)}}<extra></extra>` : `%{x}<br>%{y${hoverFormat(trace.y)}}<extra></extra>`);
    return { ...trace, name, ...(trace.type === "pie" ? { hole: 0.68, sort: false, textinfo: "none", ...trace,
      marker: { colors: palette, line: { color: surface, width: 2 }, ...marker } } : {
      hovertemplate: dark && isSeries(trace) ? null : hovertemplate,
      ...(lineChart ? {mode:'lines+markers',connectgaps:false,
        ...(dark ? { fill: trace.fill || 'tozeroy', fillgradient: { type: 'vertical', colorscale: [[0,chartRgba(palette[index % palette.length],0)],[1,chartRgba(palette[index % palette.length],.25)]] },
          fillcolor: chartRgba(palette[index % palette.length],.125) } : {})} : {}),
      marker: { size: lineChart ? 6 : 7, ...(dark ? {...marker,color:lineChart ? surface : numericColor?marker.color:palette[index % palette.length],
        ...(lineChart ? {symbol:'circle',line:{color:palette[index % palette.length],width:1.5}} : trace.type==='bar' ? {
          line:{color:original.barmode==='stack' || original.barmode==='relative' ? surface : palette[index % palette.length],width:original.barmode==='stack' || original.barmode==='relative' ? 2 : 1},cornerradius:6} : {})} : {color:palette[index % palette.length],...marker}) },
      line: dark ? {...record(trace.line),color:palette[index % palette.length],width:2.5,...(lineChart ? {shape:'spline',smoothing:.3} : {})} : {color:palette[index % palette.length],width:2,...record(trace.line)},
      ...(customPalette ? {line:{...record(trace.line),color:palette[index%palette.length],width:2.5}} : {}),
      ...(dark && isSeries(trace) ? {hoverinfo:'none'} : {}),
      ...(trace.type==='bar' ? {width: barWidth(trace, traces, original, trace.orientation==='h'?height:width),cliponaxis:false} : {}),
    }) };
  }) as Data[];
  const xs = Array.from(new Set(traces.flatMap(trace => Array.isArray(trace.x) ? trace.x : [])));
  const dates = xs.length>0 && xs.every(value => typeof value==="string" && /^\d{4}-\d{2}(?:$|-|T)/.test(value));
  const tickBudget = Math.max(3, Math.min(8, Math.floor((width-100)/74)));
  const visibleDates = xs.filter((_,index)=>index%Math.ceil(xs.length/tickBudget)===0);
  const dateTicks = dates ? { tickmode: "array", tickvals: visibleDates, ticktext: visibleDates.map(value=>formatChartDateTick(value, xs)) } : {};
  const first=traces[0] || {};
  const retention=first.type==='heatmap' && /retention|留存/.test(JSON.stringify(spec));
  const labelAxis=(values:unknown, title:string)=>Array.isArray(values) ? {tickmode:'array',tickvals:values,
    ticktext:values.map(value=>/^\d{4}-\d{2}/.test(String(value)) ? /季度/.test(title) ? `${String(value).slice(0,4)} Q${Math.floor((Number(String(value).slice(5,7))-1)/3)+1}` : formatChartDateTick(value, values) : /^\d+$/.test(String(value)) ? Number(value)===0?'首购期':`第${value}${/季度/.test(title)?'季':'月'}` : value)} : {};
  const layout = {
    ...original, width, height, autosize: false, title: { text: "" },
    margin: {...margin,...(!marginOverride&&dates?{b:40}:{})},
    uniformtext:{minsize:11,mode:'hide'},
    paper_bgcolor: surface, plot_bgcolor: surface,
    colorway: palette, bargap: 0.45, bargroupgap: .08, funnelgap: 0.08, hovermode: traces.every(isSeries) ? analysis.horizontal ? 'y' : 'x' : "closest", dragmode: "zoom", separators: '.,',
    font: { family: "Geist, Microsoft YaHei, sans-serif", size: chartText, color: ink },
    hoverlabel: { bgcolor: dark ? "#182030" : "#ffffff", bordercolor: dark ? "rgba(255,255,255,0.14)" : "#d9e1dc", align: 'left', font: { family: "Geist, Microsoft YaHei, sans-serif", size: 13, color: dark ? '#F1F5F9' : ink } },
    showlegend: appearance.show_legend ?? (typeof original.showlegend==='boolean' ? original.showlegend : traces.length > 1 || traces.some((trace) => trace.type === "pie")),
    legend: { orientation: "h", y: 1.12, yanchor:'bottom', x: 1, xanchor:'right', font: { size: appearance.font_size||11, color: dark ? '#94A3B8' : ink }, tracegroupgap: 16, itemsizing:'constant', bgcolor:'rgba(0,0,0,0)' },
    xaxis: { ...axis(original.xaxis, analysis.horizontal), ...dateTicks,...(dates || /月份|日期|month|date/i.test(axisTitle(original.xaxis)) ? {title:{text:''}} : {}),...(retention ? labelAxis(first.x,axisTitle(original.xaxis)) : {}), ...(dark && first.type==='heatmap' ? {showgrid:false,showline:false,ticks:'',ticklabelstandoff:12,tickfont:{size:11,color:'#64748B'}} : {}) },
    yaxis: { ...axis(original.yaxis, !analysis.horizontal), ...(!independentUnits && !record(original.meta).dual_axis ? {title:{text:''}} : {}),...(retention ? {...labelAxis(first.y,axisTitle(original.yaxis)),type:'category',categoryorder:'array',categoryarray:first.y,autorange:'reversed'} : {}), ...(dark && first.type==='heatmap' ? {showgrid:false,showline:false,ticks:'',ticklabelstandoff:12,tickfont:{size:11,color:'#64748B'}} : {}) },
    ...Object.fromEntries(Object.entries(original).filter(([key])=>/^[xy]axis\d+$/.test(key)).map(([key,value])=>[key,{
      ...axis(value,key.startsWith('x') ? analysis.horizontal : !analysis.horizontal),
      ...(key.startsWith('x') ? dateTicks : {}),
    }])),
    annotations:[...records(original.annotations).map(annotation=>({...annotation,font:{color:ink,...record(annotation.font)}})),...cellLabels,
      ...(!independentUnits && !record(original.meta).dual_axis && axisTitle(original.yaxis) ? [{text:businessLabel(axisTitle(original.yaxis)),xref:'paper',yref:'paper',x:0,y:1.04,xanchor:'left',yanchor:'bottom',showarrow:false,font:{size:11,color:dark?'#64748B':ink},textangle:0}] : []),
      ...(analysis.average != null ? [{text:`${analysis.additive && traces.length>1 ? '合计均值' : '均值'} ${chartNumber(analysis.average,analysis.title,true)}`,xref:analysis.horizontal?'x':'paper',yref:analysis.horizontal?'paper':'y',x:analysis.horizontal?analysis.average:1,y:analysis.horizontal?1:analysis.average,xanchor:'right',yanchor:'bottom',showarrow:false,font:{size:10,color:dark?'#94A3B8':ink},bgcolor:surface,borderpad:3}] : []),
      ...(analysis.additive && traces.every(trace=>measureValues(trace).every(value=>!finite(value) || value>=0)) && (original.barmode==='stack' || original.barmode==='relative') ? analysis.totals.map(point=>({x:analysis.horizontal?point.value:point.category,y:analysis.horizontal?point.category:point.value,xref:'x',yref:'y',text:(point.complete?'':'已知 ')+chartNumber(point.value,analysis.title,true),showarrow:false,xanchor:analysis.horizontal?'left':'center',yanchor:analysis.horizontal?'middle':'bottom',xshift:analysis.horizontal?8:0,yshift:analysis.horizontal?0:6,font:{family:chartMono,size:11,color:dark?'#E2E8F0':ink,weight:600}})) : [])],
    shapes:[...records(original.shapes),...(analysis.average != null ? [{type:'line',xref:analysis.horizontal?'x':'paper',yref:analysis.horizontal?'paper':'y',x0:analysis.horizontal?analysis.average:0,x1:analysis.horizontal?analysis.average:1,y0:analysis.horizontal?0:analysis.average,y1:analysis.horizontal?1:analysis.average,line:{color:dark?'rgba(255,255,255,0.25)':'rgba(67,87,77,.3)',width:1,dash:'dash'},layer:'above'}] : [])],
    uirevision: "preserve-chart-interaction",
  } as Partial<Layout>;
  if(record(original.meta).packed_bubble){
    layout.xaxis={visible:false,range:[0,width-margin.l-margin.r],fixedrange:true};layout.yaxis={visible:false,range:[0,height-margin.t-margin.b],fixedrange:true};layout.annotations=[];
  }
  if(traces.some(trace=>record(trace.meta).area_gap_split)){
    layout.margin={...layout.margin,b:Math.max(layout.margin?.b||0,90)};
    const note='未知期间留空，不按零填补；堆叠只显示各系列均已知的期间。';
    layout.annotations=[...(layout.annotations||[]),{text:width<650?note.match(/.{1,20}/gu)!.join('<br>'):note,xref:'paper',yref:'paper',x:0,y:0,yshift:-62,xanchor:'left',yanchor:'top',showarrow:false,font:{size:10,color:ink}}];
  }
  if(original.map){const map=record(original.map),bounds=record(original.meta).geo_bounds as number[];
    const mercator=(latitude:number)=>Math.log(Math.tan(Math.PI/4+latitude*Math.PI/360))/(2*Math.PI);
    const dx=bounds?Math.max((bounds[2]-bounds[0])/360,1e-5):1,dy=bounds?Math.max(mercator(bounds[3])-mercator(bounds[1]),1e-5):1;
    const zoom=bounds?Math.max(0,Math.min(14,Math.log2(Math.min((width-100)/(512*dx),(height-80)/(512*dy)))-.7)):map.zoom;
    const geoTrace=traces[0],labels=(geoTrace.type==='scattermap'?geoTrace.text:geoTrace.locations) as unknown[];
    const escape=(value:unknown)=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
    const labelsText=labels.slice(0,6).map((label,i)=>geoTrace.type==='scattermap'?`<span style="color:${palette[i%palette.length]}">●</span> ${escape(label)} (${(geoTrace.lon as number[])[i]}, ${(geoTrace.lat as number[])[i]})`:`${escape(label)}：${(geoTrace.z as unknown[])[i]??'未知'}`).join(width<650?'<br>':'　');
    layout.annotations=[...(layout.annotations||[]),{text:labelsText+(labels.length>6?'　其余地区见悬停与完整结果':''),xref:'paper',yref:'paper',x:0,y:1,xanchor:'left',yanchor:'top',showarrow:false,font:{color:ink,size:12}}];
    layout.map={...map,zoom,style:{version:8,sources:{'local-empty':{type:'geojson',data:{type:'FeatureCollection',features:[]}}},layers:[{id:'background',type:'background',paint:{'background-color':surface}}]}} as unknown as Partial<Layout>['map'];}
  if(record(original.meta).box_focus){
    layout.showlegend=false;
    if(width<650){
      layout.xaxis={...layout.xaxis,domain:[0,1]};layout.yaxis={...layout.yaxis,domain:[.52,1]};
      layout.xaxis2={...layout.xaxis2,domain:[0,1]};layout.yaxis2={...layout.yaxis2,domain:[0,.28],side:'left',title:{text:''}};
      layout.annotations=layout.annotations?.map(annotation=>annotation.text==='全量范围'?{...annotation,x:.5,y:.31}:String(annotation.text).startsWith('主体分布')?{...annotation,x:.5}:annotation);
    }
  }
  for(const coordinate of ['x','y'] as const){
    const relevant=traces.filter(trace=>(trace.type==='bar'&&(trace.orientation==='h'?coordinate==='y':coordinate==='x'))||trace.type==='box'&&coordinate==='x');
    const values=Array.from(new Set(relevant.flatMap(trace=>Array.isArray(trace[coordinate])?trace[coordinate] as unknown[]:[])));
    if(!values.length||!values.every(value=>typeof value==='string'&&!/^\d{4}-\d{2}/.test(value)))continue;
    if(values.length<=20&&!values.some(value=>String(value).length>14))continue;
    const budget=Math.max(3,Math.floor(coordinate==='y'?(height-margin.t-margin.b)/30:(width-margin.l-margin.r)/90));
    const selected=values.filter((_,index)=>index%Math.max(1,Math.ceil(values.length/budget))===0);
    const characters=coordinate==='y'?Math.max(6,Math.min(20,Math.floor((margin.l-20)/chartText))):12;
    const key=coordinate==='y'?'yaxis':'xaxis';
    layout[key]={...layout[key],tickmode:'array',tickvals:selected as string[],ticktext:selected.map(value=>chartLabel(value,characters)),tickangle:0};
  }
  return { data, layout };
}

function barWidth(trace: RecordValue, traces: RecordValue[], layout: RecordValue, width: number) {
  const values = categoryValues(trace);
  if (values.length<2) return trace.width;
  const categorical=record(layout[trace.orientation==='h'?'yaxis':'xaxis']).type==='category';
  const positions = values.map(value => categorical ? NaN : typeof value==='number' ? value : /^\d{4}-\d{2}/.test(String(value)) ? Date.parse(String(value)) : NaN);
  const gaps = positions.slice(1).map((value,index)=>Math.abs(value-positions[index])).filter(value=>value>0).sort((a,b)=>a-b);
  const step = gaps.length ? gaps[Math.floor(gaps.length/2)] : 1;
  const groups = layout.barmode==='stack' || layout.barmode==='relative' ? 1 : traces.filter(other=>other.type==='bar' &&
    (other.xaxis||'x')===(trace.xaxis||'x') && (other.yaxis||'y')===(trace.yaxis||'y')).length || 1;
  return Math.min(step * .55 / groups, step * values.length * 28 / Math.max(100,width-82));
}
