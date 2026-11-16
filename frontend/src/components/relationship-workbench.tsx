"use client";

import { useEffect, useRef, useState } from 'react';
import { ArrowRight, Check, GitBranch } from 'lucide-react';
import { workbenchRequest,readAnalysis,cancelAnalysis,type AnalysisTask, type WorkbenchSnapshot } from '@/lib/api';
import {TableRelationshipMap,type TableRelation} from './table-relationship-map';
import {TableStructureInspector} from './table-structure-inspector';

interface Relation {
  left_table: string; right_table: string; left_keys: string[]; right_keys: string[];
  left_grain: string; right_grain: string; source?: string;
}
const relationKey=(relation:Relation)=>JSON.stringify([[relation.left_table,relation.left_keys],[relation.right_table,relation.right_keys]].sort(([a],[b])=>String(a).localeCompare(String(b))));
interface Report extends Relation {
  snapshot:string;match_status?:string;
  cardinality: string; left_null_rows: number; right_null_rows: number;
  left_known_key_rows?:number;right_known_key_rows?:number;left_keys_unique?:boolean|null;right_keys_unique?:boolean|null;join_multiplier?:number|null;
  left_unmatched_rows: number; right_unmatched_rows: number;
  left_duplicate_keys: number; right_duplicate_keys: number;
  actual_join_rows: number; expected_join_rows: number; warnings: string[];
}
interface CheckedRelation {candidate:Relation&{reason?:string};report:Report|null;error:string}
interface Understanding {
  snapshot:string;source:string;tables:{table_name:string;purpose:string;grain:string;reason:string}[];
  candidates:CheckedRelation[];independent_tables:{table_name:string;reason:string}[];questions:string[];warnings:string[];
  coverage:{tables_included:number;tables_total:number;columns_truncated:boolean};
}
interface Overview {
  snapshot:string;candidates:Relation[];confirmed:Report[];reports:CheckedRelation[];understanding:Understanding|null;scope:string;
  tables:{table_name:string;rows:number;columns:string[];source:WorkbenchSnapshot['tables'][number]['source'];key_fields:string[];grain:string;metadata_status:string}[];
  coverage:{candidate_limit:number;checked_candidates:number;truncated:boolean;unchecked_candidates:number};
}

function RelationshipEvidence({report}:{report:Report}){
  return <><div className="cleaning-impact"><strong>{report.cardinality}</strong><span>匹配左表 {report.expected_join_rows} 行 <ArrowRight size={13} className="inline" /> 实际关联 {report.actual_join_rows} 行</span></div>
    <table><thead><tr><th>检查项</th><th>左表</th><th>右表</th></tr></thead><tbody>
      <tr><td>已知键行</td><td>{report.left_known_key_rows??'未记录'}</td><td>{report.right_known_key_rows??'未记录'}</td></tr>
      <tr><td>键唯一</td><td>{report.left_keys_unique==null?'无法判断':report.left_keys_unique?'是':'否'}</td><td>{report.right_keys_unique==null?'无法判断':report.right_keys_unique?'是':'否'}</td></tr>
      <tr><td>键缺失行</td><td>{report.left_null_rows}</td><td>{report.right_null_rows}</td></tr><tr><td>未匹配行</td><td>{report.left_unmatched_rows}</td><td>{report.right_unmatched_rows}</td></tr><tr><td>重复键组</td><td>{report.left_duplicate_keys}</td><td>{report.right_duplicate_keys}</td></tr>
    </tbody></table><p className="cleaning-note">左表匹配记录的关联倍数：{report.join_multiplier==null?'不适用':`${report.join_multiplier.toLocaleString('zh-CN',{maximumFractionDigits:3})} 倍`}</p>
    {report.warnings.map(warning=><p className="cleaning-note" key={warning}>{warning}</p>)}</>;
}

function RelationshipRow({item,onChoose,disabled}:{item:CheckedRelation;onChoose:(relation:Relation)=>void;disabled:boolean}){
  return <details className="relationship-row"><summary><span>{item.candidate.left_table}<small>{item.candidate.left_keys.join(' + ')}</small></span><ArrowRight size={15}/><span>{item.candidate.right_table}<small>{item.candidate.right_keys.join(' + ')}</small></span><em>{item.error?'待处理':item.report?.cardinality||'未核对'}</em></summary>
    <div className="relationship-row-detail"><p className="cleaning-note">{item.candidate.source}</p>{item.candidate.reason&&<p>{item.candidate.reason}</p>}{item.error?<p role="status" className="cleaning-alert">{item.error}</p>:item.report&&<RelationshipEvidence report={item.report}/>}
      <button className="toolbar-button" disabled={disabled} onClick={()=>onChoose(item.candidate)}>选用字段，继续核对与确认</button></div></details>;
}

export function RelationshipWorkbench({ sessionId, data, disabled, onUpdated, onBusyChange,onTaskStarted,selectedTable,onSelectTable }: {
  sessionId: string; data: WorkbenchSnapshot; disabled: boolean; onUpdated: (data: WorkbenchSnapshot) => void; onBusyChange: (busy: boolean) => void;
  onTaskStarted?:(task:AnalysisTask)=>void;
  selectedTable?:string;onSelectTable?:(name:string)=>void;
}) {
  const [overview,setOverview]=useState<Overview|null>(null);
  const current=overview?.snapshot===data.snapshot?overview:null;
  const candidates=current?.candidates||[],confirmed=current?.confirmed||[],understanding=current?.understanding;
  const [relation, setRelation] = useState<Relation>({ left_table: data.tables[0]?.table_name || '', right_table: data.tables[1]?.table_name || '', left_keys: [], right_keys: [], left_grain: '', right_grain: '' });
  const [report, setReport] = useState<Report | null>(null);
  const reportSection=useRef<HTMLElement>(null);
  useEffect(()=>{if(report)reportSection.current?.scrollIntoView({block:'nearest',behavior:'instant'});},[report]);
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [aiError,setAiError]=useState('');
  const [aiTask,setAiTask]=useState<string|null>(null);
  const [localSelected,setLocalSelected]=useState(data.tables[0]?.table_name||'');
  const selected=data.tables.find(table=>table.table_name===(selectedTable||localSelected))||data.tables[0];
  const graphItems:{id:string;item:CheckedRelation;confirmed:boolean}[]=[];
  const seen=new Set<string>();
  for(const entry of [...confirmed.map((report,index)=>({id:`confirmed-${index}`,item:{candidate:{...report,source:'用户业务确认'},report,error:''},confirmed:true})),
    ...(understanding?.candidates||[]).map((item,index)=>({id:`ai-${index}`,item,confirmed:false})),
    ...candidates.map((candidate,index)=>({id:`field-${index}`,item:current?.reports.find(item=>relationKey(item.candidate)===relationKey(candidate))||{candidate,report:null,error:''},confirmed:false}))]){
    const c=entry.item.candidate;
    if(c.left_keys.length!==c.right_keys.length||!c.left_keys.length||!c.left_keys.every(key=>data.tables.find(t=>t.table_name===c.left_table)?.columns.includes(key))||!c.right_keys.every(key=>data.tables.find(t=>t.table_name===c.right_table)?.columns.includes(key)))continue;
    const identity=relationKey(c);
    if(!seen.has(identity)){seen.add(identity);graphItems.push(entry);}
  }
  const graph:TableRelation[]=graphItems.map(entry=>{const c=entry.item.candidate,r=entry.item.report;
    const bad=!!entry.item.error||!!r&&(r.match_status?r.match_status!=='matched':r.actual_join_rows<=0);
    const state=bad?'unmatched':entry.confirmed?'confirmed':c.source?.startsWith('数据库外键声明')?'declared':'suggested';
    return{id:entry.id,left_table:c.left_table,right_table:c.right_table,left_keys:c.left_keys,right_keys:c.right_keys,state,cardinality:r?.cardinality,
      label:entry.confirmed?'用户已确认':bad?'未匹配 / 待处理':r?`${r.cardinality} · 键匹配，待业务确认`:c.source||'待核对候选'};});
  function selectTable(name:string){setLocalSelected(name);onSelectTable?.(name);}
  useEffect(() => {
    let active = true;
    workbenchRequest<Overview>(sessionId, 'relationships').then(result => {
      if (active) setOverview(result);
    }).catch(err => { if (active) setError(err.message); });
    return () => { active = false; };
  }, [sessionId, data.snapshot]);
  function change(update: Partial<Relation>) { setRelation({ ...relation, ...Object.fromEntries(Object.entries(update).filter(([key])=>['left_table','right_table','left_keys','right_keys','left_grain','right_grain','source'].includes(key))) }); setReport(null); setAccepted(false); setError(''); }
  async function run(confirm = false) {
    setBusy(true); onBusyChange(true); setError('');
    if(!confirm)setAccepted(false);
    try {
      const result = await workbenchRequest<Report>(sessionId, `relationships/${confirm ? 'confirm' : 'check'}`, { ...relation, source: undefined, snapshot: data.snapshot, confirmed: confirm && accepted });
      setReport(result);
      if (confirm) { setOverview(await workbenchRequest<Overview>(sessionId, 'relationships')); setAccepted(false); }
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); onBusyChange(false); }
  }
  async function understand(){
    setBusy(true);onBusyChange(true);setAiError('');
    try{
      let task=await workbenchRequest<AnalysisTask>(sessionId,'relationships/understand',{snapshot:data.snapshot});setAiTask(task.task_id);onTaskStarted?.(task);
      while(['queued','running','cancelling'].includes(task.status)){await new Promise(resolve=>setTimeout(resolve,500));task=await readAnalysis(task.task_id);}
      if(task.status!=='succeeded'||task.result?.error)throw new Error(task.error||task.result?.error||'关系理解未完成；基础概览和已有结果保留。');
      const failed=task.result?.workflow?.steps.find(step=>step.status==='failed');
      if(failed)throw new Error(failed.error||failed.result?.error||'AI 理解未完成，基础概览和手动核对仍可使用。');
      setOverview(await workbenchRequest<Overview>(sessionId,'relationships'));
    }
    catch(err){setAiError(err instanceof Error?err.message:'AI 理解暂未完成，基础概览和手动检查仍可使用。');}
    finally{setAiTask(null);setBusy(false);onBusyChange(false);}
  }
  async function confirmProfile() {
    setBusy(true); onBusyChange(true); setError('');
    try { onUpdated(await workbenchRequest<WorkbenchSnapshot>(sessionId, 'commerce/confirm', { profile: 'olist_v1', currency: 'BRL', snapshot: data.snapshot, confirmed: true })); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); onBusyChange(false); }
  }
  return <div className="relationship-workbench">
    <div className="cleaning-section-title"><h3><GitBranch size={17} />表关系概览</h3><button className="toolbar-button" disabled={disabled||busy||!!understanding} onClick={()=>void understand()}>{busy?'正在处理…':understanding?'本版已理解':'理解表关系'}</button></div>
    <p className="cleaning-note">AI 解释用途与关联线索，程序核对实际键值；业务含义由你确认。调用当前模型，同一数据版本复用。</p>
    {aiError&&<p role="alert" className="cleaning-alert">{aiError}</p>}
    {aiTask&&<button className="toolbar-button" onClick={()=>void cancelAnalysis(aiTask).catch(err=>setAiError(err.message))}>停止理解</button>}
    <TableRelationshipMap tables={data.tables} relations={graph} selected={selected?.table_name||''} onSelect={selectTable} onSelectRelation={id=>{const entry=graphItems.find(item=>item.id===id);if(entry){change(entry.item.candidate);setReport(entry.item.report);setError(entry.item.error);selectTable(entry.item.candidate.left_table);}}}/>
    {selected&&<TableStructureInspector key={`${data.snapshot}:${selected.table_name}`} table={selected} sessionId={sessionId} snapshot={data.snapshot}
      grain={current?.tables.find(item=>item.table_name===selected.table_name)?.grain?`已确认粒度：${current.tables.find(item=>item.table_name===selected.table_name)!.grain}`:understanding?.tables.find(item=>item.table_name===selected.table_name)?.grain?`AI 粒度推测：${understanding.tables.find(item=>item.table_name===selected.table_name)!.grain}`:'每行的业务粒度待确认'}
      purpose={understanding?.tables.find(item=>item.table_name===selected.table_name)?.purpose?`AI 用途推测：${understanding.tables.find(item=>item.table_name===selected.table_name)!.purpose}`:undefined} independent={understanding?.independent_tables.find(item=>item.table_name===selected.table_name)?.reason}/>}
    {current&&!candidates.length&&!understanding?.candidates.length&&<p className="relationship-empty" role="status">当前字段线索未发现可用关联。表可以独立分析，也可用 AI 理解用途，或手动选择异名/复合键；这不表示数据有错。</p>}
    {understanding&&<section className="relationship-ai" aria-label="AI 表关系理解"><p className="cleaning-note">{understanding.source}</p>
      {understanding.independent_tables.map(table=><p className="relationship-independent" key={table.table_name}>{table.table_name} · 可独立分析：{table.reason}</p>)}
      {understanding.candidates.map((item,index)=><RelationshipRow key={index} item={item} disabled={disabled||busy} onChoose={change}/>)}
      {understanding.questions.length>0&&<div className="relationship-questions"><strong>需要确认的业务问题</strong>{understanding.questions.map(question=><p key={question}>{question}</p>)}</div>}
      {understanding.warnings.map(warning=><p className="cleaning-alert" key={warning}>{warning}</p>)}
      {(understanding.coverage.tables_included<understanding.coverage.tables_total||understanding.coverage.columns_truncated)&&<p className="cleaning-alert">本次 AI 结构摘要仅含 {understanding.coverage.tables_included}/{understanding.coverage.tables_total} 张表，每表最多60字段；未穷尽全部关系。</p>}
    </section>}
    {current&&<section className="relationship-program" aria-label="程序关联线索">{current.reports.map((item,index)=><RelationshipRow key={index} item={item} disabled={disabled||busy} onChoose={change}/>)}
      <p className="cleaning-note">{current.scope}</p>{(current.coverage.truncated||current.coverage.unchecked_candidates>0)&&<p className="cleaning-alert">候选最多 {current.coverage.candidate_limit} 条，已自动核对 {current.coverage.checked_candidates} 条，另有 {current.coverage.unchecked_candidates} 条可手动核对{current.coverage.truncated?'；候选扫描已达上限，覆盖不完整':''}。</p>}
    </section>}
    {!!candidates.length && <label className="relation-candidates">候选关系<select aria-label="候选关系" defaultValue="" disabled={disabled || busy} onChange={e => { if (e.target.value !== '') change(candidates[Number(e.target.value)]); }}><option value="">选择候选，或在下方自行设置</option>{candidates.map((item, index) => <option value={index} key={index}>{item.left_table}.{item.left_keys.join('+')} → {item.right_table}.{item.right_keys.join('+')} · {item.source}</option>)}</select></label>}
    <fieldset className="relation-editor" disabled={disabled || busy}>{(['left', 'right'] as const).map(side => {
      const name = relation[`${side}_table`]; const keys = relation[`${side}_keys`];
      return <div key={side}><label>{side === 'left' ? '左表' : '右表'}<select aria-label={side === 'left' ? '左表' : '右表'} value={name} onChange={e => change({ [`${side}_table`]: e.target.value, [`${side}_keys`]: [] })}>{data.tables.map(t => <option key={t.table_name}>{t.table_name}</option>)}</select></label>
        <span>关联字段（复合键按勾选顺序一一对应）</span><div className="cleaning-keys">{data.tables.find(t => t.table_name === name)?.columns.map(column => <label key={column}><input type="checkbox" checked={keys.includes(column)} onChange={e => change({ [`${side}_keys`]: e.target.checked ? [...keys, column] : keys.filter(k => k !== column) })} />{column}</label>)}</div>
        <p className="cleaning-note">已选顺序：{keys.join(' + ') || '尚未选择'}</p>
        <label>每行代表什么<input aria-label={side === 'left' ? '左表粒度' : '右表粒度'} value={relation[`${side}_grain`]} onChange={e => change({ [`${side}_grain`]: e.target.value })} placeholder="例如：一笔订单 / 一条商品明细" /></label></div>;
    })}</fieldset>
    <button className="toolbar-button" disabled={disabled || busy || !relation.left_keys.length || !relation.right_keys.length} onClick={() => run()}>检查这条关联</button>
    {error && <p role="alert" className="cleaning-alert">{error}</p>}
    {report?.snapshot===data.snapshot && <section className="relation-report" ref={reportSection}><RelationshipEvidence report={report}/>
      <label className="relation-confirm"><input type="checkbox" disabled={disabled || busy||report.match_status!=='matched'} checked={accepted} onChange={e => setAccepted(e.target.checked)} />我已核对两边粒度和以上风险，确认这条业务关系</label><button className="toolbar-button" disabled={disabled || busy ||report.match_status!=='matched'|| !accepted || !relation.left_grain.trim() || !relation.right_grain.trim()} onClick={() => run(true)}><Check size={14} />保存确认</button></section>}
    <p className="cleaning-note">当前版本已确认 {confirmed.length} 条关联。上传、清洗后会失效，需要重新检查。</p>
    {confirmed.map((r, i) => <p className="relation-saved" key={i}><Check size={12} />{r.left_table} ({r.left_keys.join('+')}) → {r.right_table} ({r.right_keys.join('+')}) · {r.cardinality}</p>)}
    {(data.commerce.profile||data.commerce.confirmed)&&<section className="commerce-overview"><div className="cleaning-section-title"><h3>电商指标口径</h3><span>{data.commerce.confirmed ? '已确认 Olist / BRL 规则' : '结构候选，尚未确认'}</span></div><p className="cleaning-note">Olist 模板将金额按 BRL 计算，默认使用已送达订单，跨订单客户用 customer_unique_id。结构相似不能证明数据来源或币种；自有数据不符合此口径时，保留通用分析。</p>
      {data.commerce.profile && !data.commerce.confirmed && <button className="toolbar-button" disabled={disabled || busy} onClick={confirmProfile}>我确认采用 Olist 规则与 BRL 币种</button>}
      {data.commerce.confirmed && <><div className="commerce-facts">{data.commerce.facts.map(fact => <div key={fact.id} title={fact.definition}><span>{fact.label}</span><strong>{fact.value == null ? '未知' : fact.unit === 'ratio' ? `${(Number(fact.value) * 100).toFixed(2)}%` : Number(fact.value).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}<small>{fact.unit === 'ratio' ? '' : fact.unit}</small></strong><p>{fact.definition}</p></div>)}</div><p className="cleaning-note">{data.commerce.scope}</p></>}
      {data.commerce.warnings.map(warning => <p className="cleaning-note" key={warning}>{warning}</p>)}
    </section>}
  </div>;
}
