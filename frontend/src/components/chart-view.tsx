"use client";

import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useTheme } from "next-themes";
import dynamic from "next/dynamic";
import { Download, Maximize2, RotateCcw, X, Plus,SlidersHorizontal } from "lucide-react";
import {applyChartAppearance,type ChartAppearance} from '@/lib/chart-appearance';
import {ChartAppearanceEditor} from './chart-appearance-editor';
import { chartAnalysis, chartFigure, chartTitle, chartTooltip,chartReadability,chartExportFigure } from "@/lib/chart";
import { downloadChartPng, finishChart } from "@/lib/chart-effects";
import type { PlotHoverEvent } from 'plotly.js';
import type { RecordValue } from "@/lib/analysis";

const Plot = dynamic(async () => {
  const [{ default: factory }, { default: Plotly }] = await Promise.all([
    import("react-plotly.js/factory"), import("@/lib/plotly"),
  ]);
  return factory(Plotly);
}, {
  ssr: false, loading: () => <div className="chart-loading">正在准备交互图表…</div>,
});

const plotConfig = { displayModeBar: false, displaylogo: false, showTips: false, responsive: true, scrollZoom: true, doubleClick: 'reset' as const };

function ChartCanvas({ spec, expanded = false, onToggle,onReady }: { spec: RecordValue; expanded?: boolean; onToggle?: (index: number) => void;onReady?:()=>void }) {
  const frame = useRef<HTMLDivElement>(null);
  const container = useRef<HTMLDivElement>(null);
  const plot = useRef<HTMLElement | null>(null);
  const [size, setSize] = useState({ width: 500, height: 320 });
  const [error, setError] = useState("");
  const [exporting, setExporting] = useState(false);
  const [tooltip, setTooltip] = useState<ReturnType<typeof chartTooltip> | null>(null);
  const [pointer, setPointer] = useState({ x: 0, y: 0 });
  const { resolvedTheme } = useTheme();
  const figure = useMemo(() => chartFigure(spec, resolvedTheme === "dark", size.width, size.height), [spec, resolvedTheme, size]);
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => {
      const width = Math.round(entry.contentRect.width);
      const height = Math.round(entry.contentRect.height);
      if (width > 0 && height > 0) setSize((previous) => previous.width === width && previous.height === height ? previous : { width, height });
    });
    if (container.current) observer.observe(container.current);
    return () => observer.disconnect();
  }, []);

  async function resetZoom() {
    if (!plot.current) return;
    setError("");
    try {
      const { default: Plotly } = await import("@/lib/plotly");
      if((spec.data as RecordValue[]).some(trace=>['treemap','sunburst'].includes(String(trace.type)))){
        await Plotly.restyle(plot.current,{level:''});return;
      }
      const baseline = chartFigure(spec, resolvedTheme === "dark", size.width, size.height).layout;
      if(baseline.map){await Plotly.relayout(plot.current,{map:{center:baseline.map.center,zoom:baseline.map.zoom}});return;}
      await Plotly.relayout(plot.current, Object.fromEntries(Object.entries(baseline).filter(([key])=>/^[xy]axis\d*$/.test(key))
        .map(([key,value])=>Array.isArray((value as RecordValue).range)?[key,{...(value as RecordValue),autorange:false}]
          :(value as {autorange?:unknown}).autorange === 'reversed' ? [key,{...(value as RecordValue),autorange:'reversed'}] : [`${key}.autorange`,true])));
    } catch {
      setError("缩放重置失败，请重试。");
    }
  }

  async function download() {
    if (!plot.current) return;
    setExporting(true); setError("");
    try {
      const exported = chartExportFigure(spec, resolvedTheme === 'dark');
      const filename = chartTitle(spec).replace(/[<>:"/\\|?*\u0000-\u001f]/g, "-").slice(0, 80) || "data-analysis-chart";
      await downloadChartPng(exported, resolvedTheme === 'dark', filename);
    } catch {
      setError("图表下载失败，请重试。");
    } finally { setExporting(false); }
  }

  function hover(event: PlotHoverEvent) {
    if (resolvedTheme !== 'dark') return;
    const point = event.points[0];
    if (!point || !['bar','scatter'].includes(String(point.data?.type))) return;
    const source = figure.data[point.curveNumber] as RecordValue;
    if((source.meta as RecordValue)?.family)return;
    const values = (source.orientation === 'h' ? source.y : source.x) as unknown[];
    const category = values[Number(point.pointNumber)];
    const content = chartTooltip(spec, category,figure as unknown as RecordValue);
    const mouse = event.event as MouseEvent;
    if (frame.current && Number.isFinite(mouse?.clientX)) { const bounds=frame.current.getBoundingClientRect(); setPointer({x:mouse.clientX-bounds.left,y:mouse.clientY-bounds.top}); }
    setTooltip(content.rows.length ? content : null);
  }

  return <div ref={frame} className={`chart-canvas ${expanded ? "chart-canvas-expanded" : ""}`} onMouseLeave={()=>setTooltip(null)}
    onMouseMove={event=>{ if (!tooltip || !frame.current) return; const bounds=frame.current.getBoundingClientRect(); setPointer({x:event.clientX-bounds.left,y:event.clientY-bounds.top}); }}>
    <div className="chart-tools">
      <span>{['treemap','sunburst'].includes(String((spec.data as RecordValue[])?.[0]?.type))?'点击钻取 · 点击中心或路径返回':(spec.data as RecordValue[])?.[0]?.type==='sankey'?'悬停查看完整流向与权重':(spec.data as RecordValue[])?.[0]?.type==='table'?'滚动查看表格 · 空值保留':'框选缩放 · 双击还原'}</span>
      {!['sankey','table'].includes(String((spec.data as RecordValue[])?.[0]?.type))&&<button className="icon-button" title="重置图表缩放" aria-label="重置图表缩放" onClick={resetZoom}><RotateCcw size={14} /></button>}
      <button className="icon-button" title="下载高清 PNG" aria-label="下载高清 PNG" disabled={exporting} onClick={download}><Download size={14} /></button>
    </div>
    {error && <p className="px-3 text-xs text-destructive" role="alert">{error}</p>}
    <div ref={container} className="chart-plot">
      <Plot data={figure.data} layout={figure.layout}
        config={plotConfig}
        style={{ width: "100%", height: "100%" }} onInitialized={(_, element) => { plot.current = element; finishChart(element,figure.data,resolvedTheme==='dark'); onReady?.(); }}
        onUpdate={(_, element) => { plot.current = element; finishChart(element,figure.data,resolvedTheme==='dark'); setError(''); }}
        onAfterPlot={() => { if (plot.current) finishChart(plot.current, figure.data, resolvedTheme === 'dark'); }}
        onHover={hover} onUnhover={()=>setTooltip(null)}
        onLegendClick={event=>{setTooltip(null);const trace=figure.data[event.curveNumber] as RecordValue;onToggle?.(Number((trace.meta as RecordValue)?.source_trace_index??event.curveNumber));return false;}}
        onLegendDoubleClick={()=>false}
        onError={reason => { console.error('Chart rendering failed', reason); setError("图表无法显示，请检查图表数据或重试。"); onReady?.(); }} />
    </div>
    {tooltip && <div className="chart-glass-tooltip" role="tooltip" style={{left:Math.min(Math.max(8,pointer.x-150),Math.max(8,size.width-300)),top:Math.max(30,pointer.y-170)}}>
      <strong className="chart-tooltip-title">{tooltip.heading}</strong>
      <div className="chart-tooltip-rows">{tooltip.rows.map((row,index)=><div className="chart-tooltip-row" key={index}>
        <i style={{background:row.color}}/><span>{row.name}</span><strong>{row.formatted}</strong>{row.share && <small>({row.share})</small>}
      </div>)}</div>
      {tooltip.total && <div className="chart-tooltip-total"><span>{tooltip.totalLabel}{tooltip.incomplete?'（已知值）':''}</span><strong>{tooltip.total}</strong></div>}
    </div>}
  </div>;
}

export function ChartView({ spec,onAddToPanel,onReady,appearance={},onAppearanceChange }: { spec: RecordValue;onAddToPanel?:()=>void;onReady?:()=>void;
  appearance?:ChartAppearance;onAppearanceChange?:(value:ChartAppearance)=>void|Promise<void> }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [dialogReady, setDialogReady] = useState(false);
  const [savedAppearance,setSavedAppearance]=useState(appearance),[draftAppearance,setDraftAppearance]=useState(appearance),[editing,setEditing]=useState(false);
  const [hidden, setHidden] = useState<number[]>((Array.isArray(spec.data)?spec.data as RecordValue[]:[]).flatMap((trace,index)=>trace.visible==='legendonly'?[index]:[]));
  const visibleSpec = useMemo(()=>applyChartAppearance({...spec,data:(Array.isArray(spec.data)?spec.data as RecordValue[]:[]).map((trace,index)=>({...trace,visible:trace.visible===false?false:hidden.includes(index)?'legendonly':true}))},draftAppearance),[spec,hidden,draftAppearance]);
  const summary = useMemo(()=>chartAnalysis(visibleSpec),[visibleSpec]);
  const readability=useMemo(()=>chartReadability(visibleSpec),[visibleSpec]);
  const panels = Number(((visibleSpec.layout as RecordValue)?.meta as RecordValue)?.unit_panels) || 1;
  const toggle = (index:number)=>setHidden(current=>current.includes(index)?current.filter(item=>item!==index):[...current,index]);
  const kpis = summary.kpis.length ? <div className="chart-summary"><div className="chart-kpi-strip" aria-label="图示数据摘要">{summary.kpis.map(item=><div className="chart-kpi" key={item.label}><span>{item.label}</span><strong title={item.detail}>{item.value}</strong>{item.detail && <small>{item.detail}</small>}</div>)}</div><p className="chart-summary-scope">{summary.scope}</p></div> : null;
  useEffect(() => {
    if (fullscreen) dialog.current?.showModal();
    else dialog.current?.close();
  }, [fullscreen]);
  return <>
    <div className="chart-card">
      <div className="chart-card-heading"><h3>{chartTitle(visibleSpec)}</h3>
        {onAppearanceChange&&<button className="chart-add-panel" aria-label="调整图表样式" onClick={()=>{setDraftAppearance(savedAppearance);setEditing(value=>!value);}}><SlidersHorizontal size={13}/>调整样式</button>}
        {onAddToPanel&&<button className="chart-add-panel" onClick={onAddToPanel}><Plus size={13}/>加入面板</button>}
        <button className="icon-button" aria-label="全屏查看图表" title="全屏查看图表" onClick={() => setFullscreen(true)}><Maximize2 size={15} /></button>
      </div>
      {editing&&onAppearanceChange&&<ChartAppearanceEditor initial={draftAppearance} onPreview={setDraftAppearance}
        onCancel={()=>{setDraftAppearance(savedAppearance);setEditing(false);}}
        onSave={async value=>{await onAppearanceChange(value);setSavedAppearance(value);setEditing(false);}}/>}
      {(visibleSpec.layout as RecordValue)?.meta&&((visibleSpec.layout as RecordValue).meta as RecordValue).chart_note?<p className="chart-summary-scope">{String(((visibleSpec.layout as RecordValue).meta as RecordValue).chart_note)}</p>:null}
      {kpis}
      {readability.warnings.map(warning=><p className="chart-summary-scope" key={warning} role="note">{warning}</p>)}
      <div className={`chart-resizable${panels>1?' chart-unit-panels':''}${readability.height>400?' chart-dense':''}${((visibleSpec.layout as RecordValue)?.meta as RecordValue)?.box_focus?' chart-box-focus':''}`} style={panels>1||readability.height>400?{'--chart-panel-height':`${Math.max(panels>1?panels*220+60:0,readability.height)}px`} as CSSProperties:undefined}><ChartCanvas spec={visibleSpec} onToggle={toggle} onReady={onReady}/></div>
      <div className="chart-resize-hint">拖动右下角调整高度</div>
    </div>
    <dialog ref={dialog} className="chart-dialog" aria-label={`全屏图表：${chartTitle(visibleSpec)}`} onToggle={event=>setDialogReady(event.currentTarget.open)} onCancel={(event) => { event.preventDefault(); setFullscreen(false); }} onClick={(event) => { if (event.target === dialog.current) setFullscreen(false); }}>
      <div className="chart-dialog-content"><div className="chart-card-heading"><h3>{chartTitle(visibleSpec)}</h3><button className="icon-button" aria-label="退出全屏" onClick={() => setFullscreen(false)}><X size={20} /></button></div>
        {kpis}
        {fullscreen && dialogReady && <ChartCanvas spec={visibleSpec} expanded onToggle={toggle} />}
      </div>
    </dialog>
  </>;
}
