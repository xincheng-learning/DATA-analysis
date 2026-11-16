"use client";

import { ChartNoAxesCombined, ArrowUpRight, History, Radio, GripVertical } from "lucide-react";
import { ChartView } from "@/components/chart-view";
import { SqlTimeline } from "@/components/sql-timeline";
import {AnalysisEvidence,AnalysisScopeNotice} from '@/components/analysis-evidence';
import {ResultTable} from '@/components/data-panel';
import { chartTitle } from "@/lib/chart";
import { record, records, reportSources, resultSources, type RecordValue } from "@/lib/analysis";
import type { AnalysisTask } from "@/lib/api";

interface WorkspaceMessage { role: string; content: string; metadata: RecordValue }

function chartSources(metadata:RecordValue){
  return resultSources(metadata).filter(source=>records(record(source.metadata.chart_spec).data).length>0);
}

export function AnalysisWorkspace({ messages, progress, pending = false, selection, onSelect, onJump,onAddChart,sourceSelection,onSelectSource,sessionId }: {
  messages: WorkspaceMessage[]; progress: AnalysisTask | null; selection: number | null;
  pending?: boolean; onSelect: (index: number | null) => void; onJump: (index: number) => void;
  onAddChart?:(index:number,sourcePath?:(string|number)[])=>void;
  sourceSelection?:string|null;onSelectSource?:(key:string)=>void;sessionId?:string;
}) {
  const analyses = messages.flatMap((message, index) => message.role === "assistant" ? [{ message, index }] : []);
  const charts = analyses.filter(({ message }) => chartSources(message.metadata).length > 0);
  const latest = analyses.at(-1);
  const selected = selection == null ? (latest?.index===messages.length-1 ? latest : undefined) : analyses.find((item) => item.index === selection);
  const live = (pending || !!progress) && selection == null;
  const sources=!live&&selected?reportSources(selected.message.metadata):[];
  const currentSource=sources.find(source=>source.key===sourceSelection)||sources[0];
  const currentCharts=!live&&selected?(currentSource?(currentSource.hasChart?[currentSource]:[]):chartSources(selected.message.metadata)):[];
  const chart = currentCharts.length?selected:undefined;
  const metadata:RecordValue = live ? { evidence: { trace: progress?.trace || [] } } : currentSource?.metadata || selected?.message.metadata || {};
  function question(index: number) {
    return messages.slice(0, index).findLast((message) => message.role === "user" && message.metadata.type !== "upload")?.content || "分析结果";
  }
  return <aside className="analysis-workspace" aria-label="分析工作区">
    <div className="workspace-heading"><div><h2>分析工作区</h2></div><span className="workspace-live">{live ? <><Radio size={12} /> 当前任务</> : <><History size={12} /> {selection==null ? '本轮回复' : '所选历史回复'}</>}</span></div>
    <div className="workspace-scroll">
      <div className="execution-selector"><label htmlFor="analysis-history">对应回复</label><select id="analysis-history" aria-label="选择历史分析" value={selection ?? ""} onChange={(event) => onSelect(event.target.value === "" ? null : Number(event.target.value))}><option value="">{live ? "正在运行的本轮任务" : "本轮回复（最新）"}</option>{analyses.map((item, index) => <option key={item.index} value={item.index}>第 {index + 1} 轮 · {question(item.index).slice(0, 36)}</option>)}</select></div>
      {selected && !live && <details className="execution-question"><summary>查看本次分析问题</summary><p>{question(selected.index)}</p></details>}
      {selected&&!live&&(selected.message.metadata.analysis_scope||selected.message.metadata.analysis_scope_state)?<AnalysisScopeNotice metadata={selected.message.metadata}/>:null}
      {selection != null && progress && <button className="return-live" onClick={() => onSelect(null)}><Radio size={12} /> 返回正在运行的任务</button>}
      {sources.length>1&&<div className="report-source-selector" role="group" aria-label="选择结论对应的数据">{sources.map(source=><button key={source.key} aria-pressed={source.key===currentSource?.key} onClick={()=>onSelectSource?.(source.key)} title={source.label}>{source.key} · {source.label}</button>)}</div>}
      {currentSource&&<p className="workspace-source-label">{currentSource.label}</p>}
      <section className="visualization-section" aria-label="历史图表">
        <div className="section-heading"><h3><ChartNoAxesCombined size={16} /> {selection==null ? '本轮图表' : '所选回复的图表'} <span className="count-badge">{currentCharts.length}</span></h3></div>
        {chart ? <>
          {currentCharts.map(source=><div key={`${chart.index}:${JSON.stringify(source.sourcePath)}`}><ChartView spec={record(source.metadata.chart_spec)} onAddToPanel={onAddChart&&(!currentSource||currentSource.canAdd)?()=>onAddChart(chart.index,source.sourcePath):undefined}/>
            <div className="chart-provenance"><details><summary>图表对应问题</summary><p>{question(chart.index)}</p></details><button onClick={() => { onSelect(chart.index); onJump(chart.index); }}>回到对应对话 <ArrowUpRight size={12} /></button></div>
            {records(source.metadata.checks).find(check=>check.id==='chart_source')?.scope?<p className="sample-notice">{String(records(source.metadata.checks).find(check=>check.id==='chart_source')?.scope)}</p>:
              Number(source.metadata.row_count)>records(source.metadata.rows).length&&<p className="sample-notice">图表基于已返回的前 {records(source.metadata.rows).length} 行；查询共 {Number(source.metadata.row_count).toLocaleString()} 行，未展示全量。</p>}
          </div>)}
        </> : <div className="workspace-empty chart-empty"><div className="empty-chart-icon"><ChartNoAxesCombined size={30} strokeWidth={1.2} /></div><h4>{live ? '本轮尚未生成图表' : '这条回复没有生成图表'}</h4><p>仅展示与所选回复对应的结果</p></div>}
        {charts.length>0 && <details className="chart-history-drawer"><summary>查看历史图表 · {charts.length} 张</summary><div className="chart-history" role="group" aria-label="选择历史图表">{charts.map((item, index)=><button key={item.index} aria-pressed={item.index===chart?.index} onClick={()=>onSelect(item.index)} title={question(item.index)}><span>{String(index+1).padStart(2,'0')}</span><ChartNoAxesCombined size={14}/><strong>{chartTitle(record(item.message.metadata.chart_spec))}</strong></button>)}</div></details>}
      </section>
      {!live&&Array.isArray(metadata.rows)&&Array.isArray(metadata.columns)&&metadata.columns.length>0&&!metadata.error&&!metadata.validation_failure&&<section className="workspace-result" aria-label="对应结论的完整查询结果"><h3>实际结果数据</h3><ResultTable key={`${selected?.index}:${currentSource?.key||'main'}`} rows={records(metadata.rows)} columns={metadata.columns.map(String)} totalRows={Number(metadata.row_count||0)} sessionId={sessionId} resultId={typeof metadata.result_id==='string'?metadata.result_id:undefined} resultError={typeof metadata.result_error==='string'?metadata.result_error:undefined}/></section>}
      <div className="workspace-section-divider"><GripVertical size={12} /></div>
      <details className="workspace-execution-evidence" open={live}><summary>{live?'当前执行过程':'查看 SQL / Python 与执行依据'}</summary>
        {metadata.python_code||metadata.route==='python'?<AnalysisEvidence metadata={{...metadata,session_id:sessionId}}/>:<SqlTimeline metadata={metadata} running={live} />}
      </details>
    </div>
  </aside>;
}
