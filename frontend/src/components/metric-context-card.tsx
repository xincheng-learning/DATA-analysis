"use client";

import {currentMetricDependencies,currentMetricIds} from '@/lib/delivery-trust';

type RecordValue = Record<string, unknown>;

function definitions(value: unknown): RecordValue[] {
  return Array.isArray(value) ? value.filter((item): item is RecordValue => !!item && typeof item === 'object') : [];
}

/** Surface metric definitions before/with the answer; does not invent profit. */
export function MetricContextCard({context,metadata,report}:{context?:RecordValue|null;metadata?:RecordValue;report?:RecordValue|null}){
  const ids=currentMetricIds(metadata,report);
  const catalog=definitions(context?.definitions);
  const dependencies=currentMetricDependencies(metadata,report);
  const items=ids.map(id=>catalog.find(item=>item.id===id)||{id,label:id,definition:'指标字典中暂无对应定义，需确认。'});
  if(!items.length)return null;
  return <section className="metric-context-card" aria-label="本轮指标口径">
    <div className="metric-context-heading">
      <strong>本轮涉及的指标</strong>
      <small>{String(context?.version||'参照定义')} · {items.length} 项</small>
    </div>
    <ul>{items.map((item)=>{const dependency=dependencies.find(value=>value.metric_id===item.id&&value.status!=='ready')||dependencies.find(value=>value.metric_id===item.id);return <li key={String(item.id||item.label)}>
      <strong>{String(item.label||item.id)}</strong>
      <p>{String(item.definition||'')}</p>
      <small>{dependency?.status==='needs_input'?String(dependency.reason||'需补充数据或确认口径'):dependency?.status==='ready'?'必需输入已确认，计算采用情况需核验':item.template_active?'模板可用，本轮采用情况未核验':'待映射或确认'}{item.grain?` · ${String(item.grain)}`:''}</small>
    </li>})}</ul>
    <p className="metric-context-hint">定义用于确认本轮口径；模板可用不证明本次计算符合定义。完整指标字典见分析证据。</p>
  </section>;
}
