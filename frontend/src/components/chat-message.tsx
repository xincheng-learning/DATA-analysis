"use client";

import { useState } from "react";
import Image from 'next/image';
import { User, Copy, Check, ChevronDown, ChevronUp, ChartNoAxesCombined, ArrowUpRight, Paperclip, Sparkles } from "lucide-react";
import { AnalysisEvidence,AnalysisScopeNotice } from "@/components/analysis-evidence";
import { ResultTable } from "@/components/data-panel";
import { compactAnswer } from "@/lib/analysis";
import {MessageMarkdown} from '@/components/message-markdown';
import {DataOverview,DataDictionary} from '@/components/data-overview';
import type {DataTable,DataGuideTable,CleaningPlan,WorkflowReport} from '@/lib/api';
import {CleaningCard} from '@/components/cleaning-card';
import {imageUrl,resultExportUrl,type ImageReference} from '@/lib/api';
import type {Clarification,SqlProposal,VerificationCheck} from '@/lib/api';
import {ClarificationCard} from '@/components/clarification-card';
import {SqlProposalCard} from '@/components/sql-proposal-card';
import {DeliveryTrustPanel,VerificationChecks,VerificationWarnings} from '@/components/verification-checks';
import {AnalysisNarrative} from '@/components/analysis-narrative';
import {MetricContextCard} from '@/components/metric-context-card';
import {continuationDrafts,canResumeDelivery} from '@/lib/delivery-trust';

interface ChatMessageProps {
  role: "user" | "assistant";
  content: string;
  metadata?: Record<string, unknown>;
  onViewChart?: (sourceKey?:string) => void;
  onOpenBoard?: (id:string)=>void;
  onOpenAdvanced?: (id:string)=>void;
  sessionId?: string;
  onJumpReference?: (id:number)=>void;
  cleaningDisabled?:boolean;
  cleaningRevision?:number;
  onCleaningChanged?:()=>void|Promise<void>;
  onClarificationAnswer?:(answer:string,answers?:Record<string,string>)=>Promise<boolean>;
  clarificationAnswer?:string;
  onExecuteSql?:()=>Promise<boolean>;
  proposalExecuted?:boolean;
  onConfirmCleaning?:(planId:string)=>Promise<boolean>;
  onFollowUp?:(text:string)=>void;
  onResume?:()=>Promise<boolean>;
  originalRequest?:string;
}

export function ChatMessage({ role, content, metadata, onViewChart,onOpenBoard,onOpenAdvanced, sessionId,onJumpReference,cleaningDisabled,cleaningRevision,onCleaningChanged,onClarificationAnswer,clarificationAnswer,onExecuteSql,proposalExecuted,onConfirmCleaning,onFollowUp,onResume,originalRequest='' }: ChatMessageProps) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const [tableExpanded, setTableExpanded] = useState(false);
  const spec = metadata?.chart_spec as Record<string, unknown> | undefined;
  const hasChart = Array.isArray(spec?.data) && spec.data.length > 0;
  const isUpload = metadata?.type === "upload";
  const displayContent = isUpload ? content.replace(/^📎 Uploaded: /, "📎 已上传：") : content;
  const columns = Array.isArray(metadata?.columns) ? metadata.columns.map(String) : [];
  const rows = Array.isArray(metadata?.rows) ? metadata.rows as Record<string, unknown>[] : [];
  const tablePreviews=Array.isArray(metadata?.table_previews) ? metadata.table_previews as DataTable[] : [];
  const workflow=metadata?.workflow as WorkflowReport|undefined;
  const report=metadata?.analysis_report as Record<string,unknown>|undefined;
  const hasReport=role==='assistant'&&report?.version===1;
  const dictionary=role==='assistant'&&Array.isArray(metadata?.data_dictionary)?metadata.data_dictionary as DataGuideTable[]:[];
  const requestedAnswer=(metadata?.metric_context as {requested_answer?:string}|undefined)?.requested_answer;
  const summary = role === "assistant" && !workflow && !hasReport && !metadata?.streaming ? compactAnswer(content, metadata || {}) : null;
  const images=(metadata?.context_snapshot as {images?:ImageReference[]}|undefined)?.images;
  const clarification=metadata?.clarification as Clarification|undefined;
  const proposal=metadata?.sql_proposal as SqlProposal|undefined;
  const checks=(metadata?.checks||[]) as VerificationCheck[];
  const context=metadata?.context_snapshot as {quote?:{text:string;message_id:number};board?:{title:string;version:number;items?:unknown[];chart_count?:number;note_count?:number};conversation?:{title:string;messages?:unknown[];message_ids?:number[];summary?:string;message_count?:number;mode?:string};charts?:{items?:unknown[];message_ids?:number[]}}|undefined;
  async function copy() {
    const scopeState=metadata?.analysis_scope_state as {status?:string;message?:string}|undefined;
    const scopeNote=scopeState?.status&&scopeState.status!=='current'?scopeState.message:'';
    try { await navigator.clipboard.writeText([scopeNote,summary ? [...checks.filter(check=>check.status==='warning'||check.status==='failed').map(check=>check.detail),...summary.findings,...summary.hypotheses.map(text=>'待验证：'+text),...summary.nextChecks.map(text=>'下一步：'+text),...summary.warnings].join('\n') : content].filter(Boolean).join('\n\n')); setCopied(true); setCopyError(false); setTimeout(() => setCopied(false), 1800); }
    catch { setCopyError(true); }
  }
  return <article className={`chat-message message-${role} ${isUpload ? "message-upload" : ""}`}>
    <div className="message-avatar">{isUpload ? <Paperclip size={15} /> : role === "user" ? <User size={15} /> : <Sparkles size={15} />}</div>
    <div className="message-body"><div className="message-heading"><strong>{role === "user" ? "你" : "DATA analysis"}</strong>{role === "assistant" && <span>{metadata?.streaming?'正在生成':'分析助手'}</span>}
      {content && !isUpload && !metadata?.streaming && <button className="icon-button message-copy" onClick={copy} title={copyError ? "复制失败，请手动复制" : "复制内容"} aria-label={copied ? "已复制" : "复制内容"}>{copied ? <Check size={13} /> : <Copy size={13} />}</button>}</div>
      {role==='assistant'&&(metadata?.analysis_scope||metadata?.analysis_scope_state)?<AnalysisScopeNotice metadata={metadata||{}}/>:null}
      {role==='assistant'&&checks.length>0&&<VerificationWarnings checks={checks}/>}
      {role==='assistant'&&!metadata?.streaming&&<DeliveryTrustPanel checks={checks} report={report} metadata={metadata||{}}/>}
      {role==='assistant'&&!metadata?.streaming&&<MetricContextCard context={metadata?.metric_context as Record<string,unknown>|undefined} metadata={metadata} report={report}/>}
      {hasReport&&<>{requestedAnswer&&<MessageMarkdown content={requestedAnswer}/>}<AnalysisNarrative report={report!} metadata={metadata||{}} onViewSource={onViewChart}/></>}
      {workflow?<details className="workflow-evidence" open={!hasReport}><summary>执行记录与完整结果 · {workflow.steps.length} 项</summary><section className="workflow-results" aria-label="工具执行结果"><p className="conclusion-kicker">{workflow.status==='completed'?'本次要求已执行':workflow.status==='waiting'?'等待确认后继续':'本次任务尚未全部完成'}</p>
        {workflow.steps.map((step,index)=><section className="workflow-step" key={step.id} data-status={step.status}>
          <h3>{index+1}. {step.request} <span>{{succeeded:'已执行',waiting:'等待确认',failed:'未完成',skipped:'尚未执行',cancelled:'已停止'}[step.status]||step.status}</span></h3>
          <MessageMarkdown content={step.result?.answer||step.error||'前置步骤尚未完成，未执行本步骤。'}/>
          {step.result?.checks?.length? <VerificationWarnings checks={step.result.checks}/>:null}
          {step.result?.cleaning&&sessionId&&<CleaningCard initial={step.result.cleaning} sessionId={sessionId} disabled={cleaningDisabled||workflow.status==='waiting'&&!onConfirmCleaning} revision={cleaningRevision} onChanged={onCleaningChanged} onConfirmAndContinue={workflow.status==='waiting'&&step.status==='waiting'?onConfirmCleaning:undefined}/>}
          {step.result?.table_previews?.length? <DataOverview tables={step.result.table_previews}/>:null}
          {step.result?.rows?.length&&step.result.columns?.length?<details className="answer-details"><summary>步骤查询结果 · 共 {step.result.row_count} 行</summary><ResultTable rows={step.result.rows} columns={step.result.columns} totalRows={step.result.row_count} sessionId={sessionId} resultId={step.result.result_id||undefined} resultError={step.result.result_error||undefined}/></details>:null}
          {step.result?.sql&&<details className="answer-details"><summary>查看本步骤 SQL</summary><pre>{step.result.sql}</pre></details>}
          {step.result?.python_code&&<AnalysisEvidence metadata={{...step.result,session_id:sessionId}}/>}
          {step.result?.board&&onOpenBoard&&<button type="button" className="view-chart-button" onClick={()=>onOpenBoard(step.result!.board!.id)}>打开面板“{step.result.board.title}” ↗</button>}
          {step.result?.advanced_view&&onOpenAdvanced&&<button type="button" className="view-chart-button" onClick={()=>onOpenAdvanced(step.result!.advanced_view!.id)}>打开高级视图“{step.result.advanced_view.title}” ↗</button>}
          {step.result?.download&&sessionId&&<p><a className="view-chart-button" href={resultExportUrl(sessionId,step.result.download.result_id)} download>下载完整结果 · {step.result.download.row_count} 行 CSV</a><small>公式形式的文本在导出副本中加引号转义，原数据保留。</small></p>}
        </section>)}
      </section></details>:dictionary.length?<>{requestedAnswer&&<MessageMarkdown content={requestedAnswer}/>}<DataDictionary tables={dictionary} nextSteps={metadata?.data_dictionary_next_steps as string[]|undefined}/></>:hasReport||clarification?null:summary ? <section className="conclusion-card" aria-label="分析结论"><span className="conclusion-kicker">分析结论</span>
        {summary.findings.map((line,index) => <p key={index} className={index===0 ? "conclusion-lead" : "conclusion-point"}>{line}</p>)}
        {summary.hypotheses.length>0 && <div className="mt-3 border-t border-border/60 pt-3"><span className="conclusion-kicker">待验证解释</span>{summary.hypotheses.map((text,index)=><p className="conclusion-point" key={index}>{text}</p>)}</div>}
        {summary.nextChecks.length>0 && <div className="mt-3 border-t border-border/60 pt-3"><span className="conclusion-kicker">下一步核对</span>{summary.nextChecks.map((text,index)=><p className="conclusion-point" key={index}>{text}</p>)}</div>}
        {summary.warnings.map((line,index) => <p className="conclusion-warning" key={index}>{line}</p>)}
      </section> : content && (role==='assistant' && !isUpload ? <ReplyText content={displayContent} hasProposal={!!proposal}/> : <div className="message-content">{displayContent}</div>)}
      {role==='assistant'&&clarification&&<ClarificationCard question={clarification} onAnswer={onClarificationAnswer} answered={clarificationAnswer} disabled={cleaningDisabled}/>}
      {role==='assistant'&&proposal&&<SqlProposalCard proposal={proposal} onExecute={onExecuteSql} disabled={cleaningDisabled} executed={proposalExecuted}/>}
      {role==='assistant'&&checks.length>0&&<VerificationChecks checks={checks}/>}
      {role==='user'&&context?.quote&&<details className="sent-quote"><summary>引用的结论</summary><blockquote>{context.quote.text}</blockquote>{onJumpReference&&<button onClick={()=>onJumpReference(context.quote!.message_id)}>查看原回复 ↗</button>}</details>}
      {role==='user'&&context?.board&&<details className="sent-panel"><summary>分析面板 · {context.board.title}</summary><p>发送时版本 {context.board.version} · {context.board.items?.length??(context.board.chart_count||0)+(context.board.note_count||0)} 项图表与笔记。</p></details>}
      {role==='user'&&context?.conversation&&<details className="sent-panel"><summary>聊天记忆 · {context.conversation.title}</summary>{context.conversation.summary?<><p>{context.conversation.mode==='source'?'聊天原文':'模型压缩摘要'} · 覆盖 {context.conversation.message_count} 条消息 · 按发送时内容保存</p><MessageMarkdown content={context.conversation.summary}/></>:<p>{context.conversation.messages?.length??context.conversation.message_ids?.length??'整场'} 条历史消息，发送时生成压缩摘要。</p>}</details>}
      {role==='user'&&context?.charts&&<div className="sent-panel">图表附件 · {context.charts.items?.length??context.charts.message_ids?.length??0} 张已分析图表</div>}
      {role==='user'&&sessionId&&images?.length? <div className="sent-images" aria-label="本轮图片附件">{images.map((image,index)=><a key={index} href={imageUrl(sessionId,image.id)} target="_blank" rel="noreferrer" title={`查看原图 ${image.filename}`}><Image unoptimized src={imageUrl(sessionId,image.id)} alt={image.filename} width={192} height={128}/><span>{image.filename}</span></a>)}</div>:null}
      {copyError && <p role="alert" className="text-xs text-destructive">复制失败，请选择文字手动复制。</p>}
      {role==='assistant'&&!workflow&&metadata?.cleaning!=null&&sessionId&&<CleaningCard initial={metadata.cleaning as CleaningPlan} sessionId={sessionId} disabled={cleaningDisabled} revision={cleaningRevision} onChanged={onCleaningChanged}/>}
      {role==='assistant' && !dictionary.length && tablePreviews.length>0 && <DataOverview key={String(metadata?.data_version || 'saved-preview')} tables={tablePreviews}/>}
      {role==='assistant' && !workflow && !hasReport && rows.length>0 && columns.length>0 && <section className="inline-query-preview" aria-label="本轮查询结果预览"><div className="inline-result-heading"><strong>查询结果预览</strong><div className="inline-table-actions"><span>前 {Math.min(tableExpanded ? 50 : 5,rows.length)} / {Number(metadata?.row_count || rows.length).toLocaleString()} 行</span>
        {(rows.length>5 || columns.length>6) && <button type="button" className="inline-table-toggle" aria-expanded={tableExpanded} onClick={()=>setTableExpanded(!tableExpanded)} title="在表格内滚动查看，最多展示50行">{tableExpanded ? <ChevronUp size={14}/> : <ChevronDown size={14}/>}{tableExpanded ? '收起表格' : '展开表格'}</button>}</div></div>
        <ResultTable key={`${sessionId}:${String(metadata?.result_id || metadata?.sql || '')}:inline:${tableExpanded}`} rows={rows.slice(0,tableExpanded ? 50 : 5)} columns={tableExpanded ? columns : columns.slice(0,6)} {...(tableExpanded?{totalRows:Number(metadata?.row_count||rows.length),sessionId,resultId:metadata?.result_id as string|undefined,resultError:metadata?.result_error as string|undefined}:{})}/>
      </section>}
      {!hasReport&&hasChart && onViewChart && <button className="view-chart-button" onClick={()=>onViewChart()}><ChartNoAxesCombined size={17}/><span>打开完整图表</span><ArrowUpRight size={14}/></button>}
      {!hasReport&&!hasChart&&onViewChart&&(metadata?.sql||metadata?.python_code||workflow?.steps.some(step=>step.result?.sql))?<button className="view-chart-button" onClick={()=>onViewChart()}>{metadata?.python_code?'查看 Python 执行依据':'查看结果与 SQL'} <ArrowUpRight size={14}/></button>:null}
      {role === "assistant" && (summary || metadata?.investigation) ? <details className="answer-details"><summary>完整分析说明</summary>
      {summary && <div className="answer-original"><MessageMarkdown content={summary.details}/></div>}
      {role === "assistant" && metadata?.investigation != null && <AnalysisEvidence metadata={{ ...metadata, session_id: sessionId }} />}
      </details> : null}
      {role==='assistant'&&onResume&&canResumeDelivery(metadata||{})?<div className="analysis-followups"><button type="button" className="toolbar-button" disabled={cleaningDisabled} onClick={()=>onResume()}>{workflow?'从中断处继续':'仅重试绘图'}</button></div>:
        role==='assistant'&&onFollowUp&&<div className="analysis-followups">{continuationDrafts(metadata||{},originalRequest,report).map(action=><button type="button" className="toolbar-button" key={action.label} disabled={cleaningDisabled} onClick={()=>onFollowUp(action.text)}>{action.label}</button>)}</div>}
    </div>
  </article>;
}

export function ReplyText({content,hasProposal}:{content:string;hasProposal?:boolean}){
  const boundary=content.search(/\n#{1,3}\s*详细依据/);
  const compact=boundary>=0;
  const lead=boundary>=0?content.slice(0,boundary):content;
  const visible=hasProposal?lead.replace(/```sql\s*[\s\S]*?```/i,'').trim():lead;
  return <><MessageMarkdown content={visible}/>{compact&&<details className="answer-details"><summary>完整解释与依据</summary><MessageMarkdown content={content}/></details>}</>;
}
