"use client";

import {useEffect,useRef,useState} from 'react';
import {Check,ChevronDown,ChevronUp,RotateCcw,ScanLine} from 'lucide-react';
import {workbenchRequest,type CleaningPlan} from '@/lib/api';
import {cleaningRuleText,metricChanges,cleaningNullChanges} from '@/lib/workbench';
import {ResultTable} from '@/components/data-panel';
import {TemporalEvidence} from '@/components/temporal-evidence';
import {CleaningAnalysisImpact} from '@/components/cleaning-analysis-impact';

const statusText:Record<string,string>={proposed:'尚未清洗',pending:'待你确认 · 尚未清洗',applied:'已应用新版本',undone:'已撤销',dismissed:'已暂不处理',superseded:'已有更新方案',stale:'数据版本已变化',expired:'预览已过期',invalid:'规则生成未完成'};
export function CleaningCard({initial,sessionId,disabled=false,onChanged,onConfirmAndContinue,revision=0}:{initial:CleaningPlan;sessionId:string;disabled?:boolean;onChanged?:()=>void|Promise<void>;onConfirmAndContinue?:(planId:string)=>Promise<boolean>;revision?:number}) {
  const [plan,setPlan]=useState(initial),[actionBusy,setBusy]=useState(''),[error,setError]=useState(''),[expanded,setExpanded]=useState(false),[diagnosisExpanded,setDiagnosisExpanded]=useState(false);
  const key=`${sessionId}:${initial.plan_id}:${revision}`;
  const [loadedKey,setLoadedKey]=useState<string>();
  const busy=actionBusy||(initial.plan_id&&loadedKey!==key?'读取':'');
  const alive=useRef(false);
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
  useEffect(()=>{
    if(!initial.plan_id)return;
    let active=true;
    workbenchRequest<CleaningPlan>(sessionId,`cleaning/plans/${initial.plan_id}`).then(value=>{if(active){setPlan(value);setError('');}}).catch(err=>{if(active)setError(err.message);}).finally(()=>{if(active)setLoadedKey(key);});
    return()=>{active=false;};
  },[sessionId,initial.plan_id,key]);
  async function act(action:'apply'|'dismiss'|'undo') {
    if(busy||disabled||!plan.plan_id)return;
    setBusy(action);setError('');
    try {
      if(action==='apply'&&onConfirmAndContinue){
        if(!await onConfirmAndContinue(plan.plan_id))setError('确认尚未提交成功，方案保留，请重试。');
        return;
      }
      const result=await workbenchRequest<CleaningPlan>(sessionId,`cleaning/plans/${plan.plan_id}/${action}`,{});
      if(!alive.current)return;
      setPlan(result);await onChanged?.();
    }catch(err){if(alive.current)setError(err instanceof Error?err.message:'处理未完成，请重试并核对当前状态。');}
    finally{if(alive.current)setBusy('');}
  }
  async function refresh(){
    if(busy||!plan.plan_id)return;
    setBusy('读取');
    try{const result=await workbenchRequest<CleaningPlan>(sessionId,`cleaning/plans/${plan.plan_id}`);if(alive.current){setPlan(result);setError('');}}
    catch(err){if(alive.current)setError(err instanceof Error?err.message:'状态读取失败。');}
    finally{if(alive.current)setBusy('');}
  }
  const preview=plan.preview;
  const issues=(plan.diagnosis?.tables||[]).flatMap(table=>table.fields.flatMap(field=>{
    const candidates=field.number_candidates||[],best=candidates.reduce<(typeof candidates)[number]|undefined>((selected,value)=>!selected||value.success_count>selected.success_count?value:selected,undefined);
    const format=(field.currency_markers?.length||0)>1?'多个币种标记':best?.failure_count?`${best.failure_count} 条不符合候选格式`:'—';
    return field.null_count||field.trim_count||field.null_candidates.length||format!=='—'?[{'数据表':table.table,'字段':field.column,'缺失':`${field.null_count} (${field.null_percent}%)`,'首尾空白':field.trim_count,'候选缺失标记':field.null_candidates.map(c=>`${c.value||'空文本'} × ${c.count}`).join('；')||'—','数值格式':format}]:[];
  }));
  const diffs=(preview?.examples||[]).slice(0,expanded?50:5).map((example,i)=>{
    const keys=Object.keys(example.before).filter(key=>!example.after||JSON.stringify(example.before[key])!==JSON.stringify(example.after[key]));
    return {'样例':i+1,'处理前':JSON.stringify(Object.fromEntries(keys.map(k=>[k,example.before[k]]))),'处理后':example.after?JSON.stringify(Object.fromEntries(keys.map(k=>[k,example.after![k]]))):'移除该行'};
  });
  const changes=preview?.metrics_before&&preview?.metrics_after?metricChanges(preview.metrics_before.facts,preview.metrics_after.facts):[];
  return <section className="chat-cleaning-card" aria-label="数据体检与清洗方案">
    <header><strong><ScanLine size={15}/>数据体检与清洗</strong><span className={plan.status==='applied'?'is-applied':''}>{statusText[plan.status]||'核对状态'}</span></header>
    <div className="cleaning-diagnosis">
      <div className="cleaning-inline-title"><strong>检查发现</strong><span>{(plan.diagnosis?.tables||[]).length} 张表 · 方案生成时的整表统计</span></div>
      {issues.length?<><ResultTable key={`issues:${diagnosisExpanded}`} rows={diagnosisExpanded?issues:issues.slice(0,5)} columns={['数据表','字段','缺失','首尾空白','候选缺失标记','数值格式']}/>{issues.length>5&&<button className="inline-table-toggle" aria-expanded={diagnosisExpanded} onClick={()=>setDiagnosisExpanded(!diagnosisExpanded)}>{diagnosisExpanded?'收起问题':`展开全部 ${issues.length} 项`}</button>}</>:<p>未检出缺失或文本空白问题；仍需结合业务含义判断数据是否适合分析。</p>}
      {(plan.diagnosis?.tables||[]).filter(t=>t.duplicate_rows>0).map(t=><p className="cleaning-inline-note" key={t.table}>{t.table} 有 {t.duplicate_rows} 行全行重复候选，不代表应直接删除。</p>)}
      {(plan.diagnosis?.tables||[]).map(t=><TemporalEvidence key={`${t.table}:${plan.diagnosis.snapshot}`} sessionId={sessionId} table={t.table} snapshot={plan.diagnosis.snapshot} currentSnapshot={plan.current_snapshot} checks={t.temporal_checks} summary={t.temporal_summary} disabled={disabled||!!busy}/>)}
    </div>
    {plan.notes?.length>0&&<details className="cleaning-metric-details"><summary>处理依据与注意事项 · {plan.notes.length} 项</summary><ul className="cleaning-advice-notes">{plan.notes.map((text,i)=><li key={i}>{text}</li>)}</ul></details>}
    {plan.questions?.map((text,i)=><p className="cleaning-inline-warning" key={i}>{text}</p>)}
    {preview&&<>
      <div className="cleaning-inline-title"><strong>建议处理 · {preview.table}</strong><span>{preview.before_rows.toLocaleString()} → {preview.after_rows.toLocaleString()} 行 · 影响 {preview.affected_rows.toLocaleString()} 行</span></div>
      <ol className="cleaning-proposed-rules">{preview.rules.map((rule,i)=><li key={i}>{cleaningRuleText(rule)}</li>)}</ol>
      {preview.conversion_failures>0&&<p className="cleaning-inline-warning">转换失败 {preview.conversion_failures} 条，应用后将变为空值。请核对格式和影响后决定。</p>}
      {preview.conflicting_keys>0&&<p className="cleaning-inline-warning">同键不同值 {preview.conflicting_keys} 组，请确认保留策略符合业务含义。</p>}
      <div className="inline-result-heading"><strong>处理前后对照</strong>{(preview.examples.length>5)&&<button className="inline-table-toggle" aria-expanded={expanded} onClick={()=>setExpanded(!expanded)}>{expanded?<ChevronUp size={14}/>:<ChevronDown size={14}/>} {expanded?'收起对照':'展开对照'}</button>}</div>
      <ResultTable key={`diff:${expanded}`} rows={diffs} columns={['样例','处理前','处理后']}/>
      <p className="cleaning-inline-note">展示 {diffs.length} / {preview.affected_rows} 个受影响行的对照样例，统计范围为全部规则合并后的结果。</p>
      {cleaningNullChanges(preview).length>0&&<details className="cleaning-metric-details" open><summary>字段缺失变化</summary><ResultTable rows={cleaningNullChanges(preview)} columns={['字段','处理前缺失','处理后缺失','变化']}/></details>}
      {!!preview.type_changes?.length&&<details className="cleaning-metric-details" open><summary>字段值类型变化</summary><ResultTable rows={preview.type_changes.map(item=>({'字段':item.column,'处理前类型':item.before,'处理后类型':item.after}))} columns={['字段','处理前类型','处理后类型']}/><p>字段类型取自实际值，数值零会保留。对照中的金额文本用于保留精度。</p></details>}
      {changes.length>0&&<details className="cleaning-metric-details"><summary>指标影响 · 按已识别模板试算</summary>{changes.map(change=><p key={change.label}>{change.label}：{String(change.before??'未知')} → {String(change.after??'未知')} {change.unit}</p>)}</details>}
      <CleaningAnalysisImpact impact={plan.receipt?.analysis_impact||preview.analysis_impact} state={plan.status} currentSnapshot={plan.current_snapshot}/>
      <p className="cleaning-inline-note">尚未应用时可在聊天中修改方案。原文件与已有图表快照保留。</p>
    </>}
    {error&&<p role="alert" className="cleaning-inline-warning">{error}</p>}
    {plan.record_error&&<p role="alert" className="cleaning-inline-warning">{plan.record_error}</p>}
    {plan.plan_id&&<footer>
      {['pending','proposed'].includes(plan.status)?<><button className="toolbar-button" disabled={!!busy||disabled||!!error||plan.status!=='pending'} onClick={()=>act('dismiss')}>暂不处理</button><button className="cleaning-confirm-button" disabled={!!busy||disabled||!!error||plan.status!=='pending'} onClick={()=>act('apply')}><Check size={14}/>{busy==='apply'?'正在提交…':onConfirmAndContinue?'确认清洗并继续原任务':'按此方案清洗'}</button></>:null}
      {plan.can_undo&&<button className="toolbar-button" disabled={!!busy||disabled} onClick={()=>act('undo')}><RotateCcw size={14}/>{busy==='undo'?'正在撤销…':'撤销这次清洗'}</button>}
      {['superseded','stale','expired'].includes(plan.status)&&<span>请在聊天中要求重新检查或调整方案。</span>}
      {busy==='读取'&&<span>正在核对方案状态…</span>}
      {disabled&&<span>当前任务中暂不可操作，请以最新方案为准。</span>}
      {(!!error||plan.record_error)&&<button className="toolbar-button" disabled={!!busy} onClick={refresh}>重新核对状态</button>}
    </footer>}
  </section>;
}
