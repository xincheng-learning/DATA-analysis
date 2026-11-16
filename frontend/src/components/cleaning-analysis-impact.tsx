import type {CleaningAnalysisImpact as Impact} from '@/lib/api';
import {ResultTable} from '@/components/data-panel';

export function CleaningAnalysisImpact({impact,state='preview',currentSnapshot}:{impact?:Impact;state?:string;currentSnapshot?:string}){
  if(!impact)return null;
  const historical=state==='undone'||['stale','superseded','expired'].includes(state)||(impact.phase==='applied'&&!!currentSnapshot&&impact.after_snapshot!==currentSnapshot);
  const title=historical?'历史清洗影响 · 不代表当前版本':impact.phase==='applied'?'本轮分析影响 · 已保存新版本复核':'本轮分析影响 · 预览试算，尚未应用';
  const before=impact.before?.input_coverage,after=impact.after?.input_coverage;
  const fields=[...new Set([...Object.keys(before?.missing_rows||{}),...Object.keys(after?.missing_rows||{})])];
  const coverage=[{'检查项':'筛选后的输入行数','处理前':before?.input_rows??'未核验','处理后':after?.input_rows??'未核验'},
    ...fields.map(field=>({'检查项':`${field} 未知行数`,'处理前':before?.missing_rows[field]??'未核验','处理后':after?.missing_rows[field]??'未核验'}))];
  const changes=(impact.changes||[]).map(item=>({'分组':Object.entries(item.dimensions).map(([key,value])=>`${key}=${value??'未知'}`).join('；')||'整体',
    '指标':item.metric,'处理前':item.row_status==='added'?'原分组不存在':item.before??'未知',
    '处理后':item.row_status==='removed'?'新分组不存在':item.after??'未知','差额（后减前）':item.delta??'不计算',
    '状态':item.row_status==='added'?'新增分组':item.row_status==='removed'?'移除分组':'变化'}));
  return <section className="cleaning-metric-details" aria-label="本轮分析的清洗影响"><h4>{title}</h4>
    {impact.goal&&<p>参照分析：{impact.goal}{impact.source_message_id?`（消息 ${impact.source_message_id}）`:''}</p>}
    <p>{impact.status==='verified'?'同一声明计划的前后对照已完成，不表示清洗规则或业务解释已经认证。':impact.reason||'本轮指标影响尚未核验。'}</p>
    {impact.scope&&<p>{impact.scope}</p>}
    {(before||after)&&<ResultTable rows={coverage} columns={['检查项','处理前','处理后']}/>}
    {!before&&!after&&impact.before&&<p>本轮筛选样本与缺失覆盖暂未独立核验。</p>}
    {changes.length>0?<><ResultTable rows={changes} columns={['分组','指标','处理前','处理后','差额（后减前）','状态']}/><p>展示 {changes.length} / {impact.change_count??changes.length} 项变化；未知值不按零计算。</p></>:impact.status==='verified'?<p>本轮指标值未变化；仍需查看样本与缺失变化。</p>:null}
    {(['before','after'] as const).map(side=>{const result=impact[side];return result?.status==='unverified'?<p key={side}>{side==='before'?'处理前':'处理后'}暂不能按该计划计算：{result.reason}</p>:
      impact.status!=='verified'&&result?.rows.length?<details key={side}><summary>{side==='before'?'处理前':'处理后'}可用的试算结果</summary><ResultTable rows={result.rows} columns={result.columns||[]}/></details>:null;})}
    {(['before','after'] as const).flatMap(side=>(impact[side]?.samples||[]).map(sample=><details key={`${side}-${sample.stage}`}><summary>{side==='before'?'处理前':'处理后'}样本 · {sample.rows.length} 行</summary><ResultTable rows={sample.rows} columns={sample.columns}/></details>))}
  </section>;
}
