"use client";

import {useEffect,useState} from 'react';
import {Check,RefreshCw} from 'lucide-react';
import {localRequest,type AdvancedView} from '@/lib/api';
import {advancedFigure} from '@/lib/advanced-figure';
import {businessLabel,type RecordValue} from '@/lib/analysis';
import {ChartView} from './chart-view';
import {ResultTable} from './data-panel';
import type {ChartAppearance} from '@/lib/chart-appearance';

export function AdvancedPresentation({view,revision=0}:{view:AdvancedView;revision?:number}){
  const [result,setResult]=useState<{id:string;revision:number;retry:number;rows:RecordValue[];columns:string[];figure:RecordValue|null}>();
  const [error,setError]=useState(''),[retry,setRetry]=useState(0);
  const [appearance,setAppearance]=useState<ChartAppearance>(view.appearance||{});
  const id=view.id;
  useEffect(()=>{
    let active=true;
    localRequest(`/superset/views/${id}/presentation`).then(data=>{
      const figure=advancedFigure(data.advanced_view,data.rows);
      if(active){setResult({id,revision,retry,rows:data.rows,columns:data.columns,figure});setError('');}
    }).catch(err=>{if(active){setError(err instanceof Error?err.message:'图表核对失败。');setResult(undefined);}});
    return()=>{active=false;};
  },[id,revision,retry]);
  const current=result?.id===view.id&&result.revision===revision&&result.retry===retry?result:undefined;
  if(error)return <div className="advanced-verify-error" role="alert"><strong>暂不展示未经核对的图表</strong><p>{error}</p><button onClick={()=>{setError('');setRetry(n=>n+1);}}><RefreshCw size={13}/>重新核对</button></div>;
  if(!current)return <div className="advanced-empty" role="status">正在回读 Superset，逐组核对全部 {view.row_count} 行…</div>;
  const stageCount=view.chart_type==='sankey'?Math.max(...view.dimensions.map(dim=>new Set(current.rows.map(row=>row[dim])).size)):0;
  return <div className="advanced-presentation" style={{'--advanced-chart-height':`${Math.max(530,Math.min(1800,stageCount*23))}px`} as React.CSSProperties}>
    <div className="advanced-proof"><span><Check size={14}/>全部 {current.rows.length.toLocaleString()} 行已核对</span><span>{view.dimensions.map(businessLabel).join(' → ')}<i/> {businessLabel(view.measure)}</span></div>
    {current.figure?<ChartView key={`${view.id}:${revision}:${retry}`} spec={current.figure} appearance={appearance}
      onAppearanceChange={async value=>{const saved=await localRequest(`/superset/views/${view.id}/appearance`,'PUT',value);setAppearance(saved.appearance);}}/>:<div className="advanced-pivot"><h3>{view.title}</h3><p>{view.chart_type==='pivot_table'?'每行是唯一维度组合；空值保留为未知，不补零、不重复求和。':'权重全部为零，没有可绘制的面积或流量；以下保留完整结果。'}</p><ResultTable key={view.id} rows={current.rows} columns={current.columns}/></div>}
    <p className="advanced-caption">{view.chart_type==='sankey'?'保留全部流向，线宽表示权重；悬停查看精确数值。':view.chart_type==='pivot_table'?'可滚动查看全量已核对结果。':'面积表示叶子权重，父级显示其下权重合计；小区域隐藏标签，悬停仍可查看。'}</p>
    {current.figure&&<details className="advanced-data-evidence"><summary>查看图表数据与展示范围 · {current.rows.length} 行</summary><p>数据来自 Superset 默认查询并与保存快照逐组核对；使用本产品统一绘图。原生视图的临时筛选不会自动带入。</p><ResultTable key={view.id} rows={current.rows} columns={current.columns}/></details>}
  </div>;
}
