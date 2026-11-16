"use client";

import {useState} from 'react';
import {Sparkles} from 'lucide-react';
import {saveModelSettings,type ModelSettingsStatus,type ReasoningEffort} from '@/lib/api';

export function ThinkingControl({status,disabled,onSaved,onBusy}:{status:ModelSettingsStatus|null;disabled:boolean;
  onSaved:(value:ModelSettingsStatus)=>void;onBusy:(value:boolean)=>void}){
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const supported=Boolean(status?.configured&&status.thinking_supported);
  const value=status?.thinking_mode==='enabled'?status.reasoning_effort:status?.thinking_mode==='disabled'?'disabled':'auto';
  async function change(next:string){
    if(!status||!supported||busy||disabled)return;
    setBusy(true);onBusy(true);setError('');
    try{
      onSaved(await saveModelSettings({base_url:status.base_url,model:status.model,
        thinking_mode:next==='auto'?'auto':next==='disabled'?'disabled':'enabled',
        reasoning_effort:next==='auto'||next==='disabled'?status.reasoning_effort:next as ReasoningEffort}));
    }catch(err){setError(err instanceof Error?err.message:'思考强度保存失败。');}
    finally{setBusy(false);onBusy(false);}
  }
  return <div><label className="mode-control" title="应用到当前模型的所有新请求；默认使用兼容设置，工具格式请求关闭思考，普通请求跟随服务商默认。Agent 自动选择分析路线。">
    <Sparkles size={14}/><select aria-label="模型思考强度" value={value} disabled={!supported||disabled||busy} onChange={event=>void change(event.target.value)}>
      <option value="auto">{!status?'读取思考设置…':!status.configured?'请先配置模型':!supported?'模型使用默认思考':'思考 · 默认'}</option>
      <option value="disabled">思考 · 关闭</option><option value="low">思考 · 低</option><option value="high">思考 · 高</option><option value="max">思考 · 最高</option>
    </select></label>{busy&&<small role="status">正在保存…</small>}{error&&<small role="alert">{error}</small>}</div>;
}
