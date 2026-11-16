"use client";
import {useState} from 'react';
import type {SqlProposal} from '@/lib/api';

export function SqlProposalCard({proposal,onExecute,disabled,executed}:{proposal:SqlProposal;onExecute?:()=>Promise<boolean>;disabled?:boolean;executed?:boolean}){
  const [sending,setSending]=useState(false),[error,setError]=useState('');
  return <section className="sql-proposal-card" aria-label="推荐 SQL 验证"><div className="inline-result-heading"><strong>推荐 SQL</strong><span>{executed?'已有独立验证记录':'尚未执行'}</span></div>
    <pre><code>{proposal.sql}</code></pre><p>只读执行所展示的查询，并核对实际结果。数据变化后需重新生成。</p>
    <button className="panel-action" disabled={disabled||sending||!onExecute} onClick={async()=>{setSending(true);setError('');try{if(!await onExecute!())setError('未能开始验证，请查看提示后重试。');}catch(reason){setError(reason instanceof Error?reason.message:'验证未开始。');}finally{setSending(false);}}}>{sending?'正在开始验证…':'一键只读验证'}</button>
    {error&&<p role="alert" className="text-destructive">{error}</p>}</section>;
}
