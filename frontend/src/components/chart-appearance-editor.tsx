"use client";
import {useState} from 'react';
import type {ChartAppearance} from '@/lib/chart-appearance';

export function ChartAppearanceEditor({initial,onPreview,onSave,onCancel}:{initial:ChartAppearance;
  onPreview:(value:ChartAppearance)=>void;onSave:(value:ChartAppearance)=>Promise<void>;onCancel:()=>void}){
  const [value,setValue]=useState(initial),[saving,setSaving]=useState(false),[error,setError]=useState('');
  function update(next:ChartAppearance){setValue(next);onPreview(next);}
  async function save(){setSaving(true);setError('');try{await onSave(value);}catch(reason){setError((reason as Error).message);}finally{setSaving(false);}}
  return <form className="chart-appearance-editor" aria-label="调整图表外观" onSubmit={event=>{event.preventDefault();void save();}}>
    <p>调整展示与导出效果，数据和原始口径保留。留空使用原设置。</p>
    <fieldset disabled={saving}><label>图表标题<input aria-label="图表标题" maxLength={160} value={value.title||''} onChange={event=>update({...value,title:event.target.value})}/></label>
      <div className="chart-editor-grid"><label>横轴标题<input aria-label="横轴标题" maxLength={100} value={value.x_label||''} onChange={event=>update({...value,x_label:event.target.value})}/></label>
      <label>纵轴标题<input aria-label="纵轴标题" maxLength={100} value={value.y_label||''} onChange={event=>update({...value,y_label:event.target.value})}/></label>
      <label>配色<select aria-label="图表配色" value={value.palette||''} onChange={event=>update({...value,palette:event.target.value as ChartAppearance['palette']||undefined})}><option value="">原配色</option><option value="jade">翡翠</option><option value="ocean">海洋</option><option value="sunset">日落</option></select></label>
      <label>字号<select aria-label="图表字号" value={value.font_size||''} onChange={event=>update({...value,font_size:event.target.value?Number(event.target.value):undefined})}><option value="">自动</option>{[12,14,16,18,20,24].map(size=><option value={size} key={size}>{size}</option>)}</select></label>
      <label>图例<select aria-label="图例显示" value={value.show_legend===undefined?'':String(value.show_legend)} onChange={event=>update({...value,show_legend:event.target.value===''?undefined:event.target.value==='true'})}><option value="">自动</option><option value="true">显示</option><option value="false">隐藏</option></select></label></div>
      <footer><button type="button" onClick={()=>update({})}>恢复原设置</button><button type="button" onClick={onCancel}>取消</button><button type="submit">{saving?'保存中…':'保存外观'}</button></footer>
    </fieldset>{error&&<p role="alert">保存未完成：{error}。当前预览仍保留，可重试或取消。</p>}
  </form>;
}
