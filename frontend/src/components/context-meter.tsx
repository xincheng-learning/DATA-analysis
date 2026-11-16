"use client";
import {useEffect,useState} from 'react';
import {localRequest} from '@/lib/api';

type ContextStatus={used_tokens:number;limit_tokens:number;trigger_tokens:number;compressed:boolean;summarized_messages:number};
export function ContextMeter({sessionId,revision,pending}:{sessionId:string|null;revision:number;pending:boolean}){
  const key=`${sessionId}:${revision}:${pending}`;
  const [result,setResult]=useState<{key:string;status:ContextStatus|null;failed:boolean}|null>(null);
  useEffect(()=>{let active=true;if(pending)return;localRequest(sessionId?`/sessions/${encodeURIComponent(sessionId)}/context`:'/context').then(value=>{if(active)setResult({key,status:value,failed:false});}).catch(()=>{if(active)setResult({key,status:null,failed:true});});return()=>{active=false;};},[sessionId,key,pending]);
  const status=result?.key===key?result.status:null,failed=result?.key===key&&result.failed;
  const percent=status?Math.round(status.used_tokens/status.limit_tokens*100):0;
  const label=pending?'分析进行中，用量完成后更新':failed?'上下文用量暂不可用':status?`上下文已用 ${percent}%`:'读取上下文…';
  return <details className="context-meter"><summary aria-label={label}><svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" strokeWidth="2" opacity=".18"/><circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeDasharray={`${Math.min(100,percent)*.503} 50.3`} transform="rotate(-90 12 12)"/></svg></summary><div className="context-tooltip" role="status"><strong>上下文窗口 · 会话记忆</strong>{status?<><b>{percent}% 已用（剩余 {Math.max(0,100-percent)}%）</b><span>约 {status.used_tokens.toLocaleString()} 标记，共 {status.limit_tokens.toLocaleString()}</span><small>达到 80% 时，下次发送前自动压缩。{status.compressed?`已将较早 ${status.summarized_messages} 条消息压缩。`:'最近对话优先保留原文。'}</small><small>此处为对话记忆估算，非累计计费 Token 或模型官方上限；系统指令、数据和输出另留空间。原聊天完整保留。</small></>:<span>{label}</span>}</div></details>;
}
