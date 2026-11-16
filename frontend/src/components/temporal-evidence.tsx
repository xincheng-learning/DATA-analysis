"use client";

import {useEffect,useRef,useState} from 'react';
import {workbenchRequest,resultExportUrl,type TemporalCheck,type TemporalSummary,type TemporalRows} from '@/lib/api';
import {ResultTable} from '@/components/data-panel';
import {businessLabel} from '@/lib/analysis';

export function TemporalEvidence({sessionId,table,snapshot,currentSnapshot,checks=[],summary,disabled=false}:{
  sessionId:string;table:string;snapshot:string;currentSnapshot?:string;checks?:TemporalCheck[];summary?:TemporalSummary;disabled?:boolean;
}) {
  const [result,setResult]=useState<TemporalRows>(),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const alive=useRef(false);
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
  const stale=!!currentSnapshot&&currentSnapshot!==snapshot;
  async function view(checkId:string){
    if(busy||stale||disabled)return;
    setBusy(true);setError('');
    try{
      const value=await workbenchRequest<TemporalRows>(sessionId,'workbench/temporal-rows',{table,snapshot,check_id:checkId});
      if(alive.current)setResult(value);
    }catch(err){if(alive.current){setResult(undefined);setError(err instanceof Error?err.message:'命中记录读取失败。');}}
    finally{if(alive.current)setBusy(false);}
  }
  if(!checks.length)return null;
  const title=(check:TemporalCheck)=>`${businessLabel(check.later_column)}早于${businessLabel(check.earlier_column)}`;
  return <section className="temporal-evidence" aria-label={`${table}时间检查证据`}>
    <header><strong>{table} · 时间顺序检查</strong>{summary&&<span>去重命中 {summary.affected_rows} 行 · 多规则重叠 {summary.multiple_rule_rows} 行</span>}</header>
    <p className="cleaning-note">按行统计，不等于去重订单数。时间原值保留；空值或无法解析的值不参与比较。</p>
    {stale&&<p className="cleaning-inline-warning">以下是方案生成时的原值；当前版本已变化，请重新体检后查看当前命中。</p>}
    {checks.map(check=><details key={check.id||title(check)} open={check.reversed_rows>0} className="temporal-check">
      <summary><span>{title(check)}<small>可比较 {check.eligible_rows} 行 · 未参与 {check.unavailable_rows} 行</small></span><strong>{check.reversed_rows} 行</strong></summary>
      <p className="cleaning-note">{table}.{check.earlier_column} → {table}.{check.later_column}{check.missing_rows!==undefined?`；未参与：含空值 ${check.missing_rows} 行，无空值但格式无效 ${check.invalid_rows} 行。`:''}</p>
      {!!check.examples.length&&<ResultTable rows={check.examples.map(e=>({'原表行号':e.active_row,...(e.order_id!==undefined?{order_id:e.order_id}:{}),[check.earlier_column]:e.earlier,[check.later_column]:e.later}))}
        columns={['原表行号',...(check.examples.some(e=>e.order_id!==undefined)?['order_id']:[]),check.earlier_column,check.later_column]}/>}
      {check.id&&check.reversed_rows>0&&<button type="button" className="report-source-link" disabled={busy||stale||disabled} onClick={()=>view(check.id!)} aria-label={`查看${table}.${title(check)}的全部命中`}>查看全部 {check.reversed_rows} 行命中 ↗</button>}
    </details>)}
    {!!summary?.overlaps.length&&<details className="temporal-overlaps"><summary>规则之间的共同命中</summary>{summary.overlaps.map(item=><p key={item.check_ids.join(':')}>{item.check_ids.map(id=>checks.find(check=>check.id===id)).filter((check):check is TemporalCheck=>!!check).map(title).join(' 与 ')}：{item.rows} 行</p>)}</details>}
    {!!summary?.affected_rows&&<button type="button" className="report-source-link" disabled={busy||stale||disabled} onClick={()=>view('any')}>查看去重后的全部 {summary.affected_rows} 行 ↗</button>}
    {busy&&<p role="status">正在读取命中记录…</p>}{error&&<p className="cleaning-inline-warning" role="alert">{error}</p>}
    {result&&<section className="temporal-matches" aria-label="完整时间异常命中记录"><h4>命中记录 · {result.table}</h4><p className="cleaning-note">快照 {result.snapshot.slice(0,10)} · 原表行号仅对应此版本。</p>
      <ResultTable key={result.result_id} rows={result.rows} columns={result.columns} totalRows={result.row_count} sessionId={sessionId} resultId={result.result_id}/>
      <a href={resultExportUrl(sessionId,result.result_id)} download className="report-source-link">下载完整命中 CSV · {result.row_count} 行</a>
    </section>}
  </section>;
}
