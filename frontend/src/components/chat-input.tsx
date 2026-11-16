"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import Image from 'next/image';
import { Send, Plus, Square, Quote, LayoutDashboard, X, Upload,MessageSquare,ChartNoAxesCombined } from "lucide-react";
import type {QuoteReference,PanelReference,ContextReference,ConversationReference} from '@/lib/api';

interface ChatInputProps {
  onSend: (message: string, files: File[]) => void | Promise<boolean | void | {sent:boolean;uploaded:File[]}>;
  revision?:number;
  draftRequest?:{text:string;id:number}|null;
  onStop?: () => void;
  loading?: boolean;
  disabled?: boolean;
  onConfigureModel?: () => void;
  quote?:QuoteReference|null;panel?:PanelReference|null;
  onRemoveQuote?:()=>void;onRemovePanel?:()=>void;onPickPanel?:()=>void;
  conversation?:ConversationReference|null;charts?:ContextReference|null;contextMeter?:React.ReactNode;
  onRemoveConversation?:()=>void;onRemoveCharts?:()=>void;
  onPickConversation?:()=>void;onPickCharts?:()=>void;hasCharts?:boolean;
}

const isImage=(file:File)=>/\.(png|jpe?g|webp)$/i.test(file.name);

export function ChatInput({ onSend, revision=0, draftRequest, onStop, loading, disabled, onConfigureModel,quote,panel,onRemoveQuote,onRemovePanel,onPickPanel,conversation,charts,onRemoveConversation,onRemoveCharts,onPickConversation,onPickCharts,hasCharts,contextMeter }: ChatInputProps) {
  const [input, setInput] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [menuOpen,setMenuOpen]=useState(false);
  const menuRef=useRef<HTMLDivElement>(null);
  const [pending,setPending]=useState<{file:File;preview?:string}[]>([]);
  const [fileError,setFileError]=useState('');
  const [dragging,setDragging]=useState(false);
  const previewUrls=useRef(new Set<string>());
  const lastRevision=useRef(revision);
  const submitting=useRef(false);
  const [appliedDraft,setAppliedDraft]=useState<number|null>(null);
  if(draftRequest&&draftRequest.id!==appliedDraft){
    setAppliedDraft(draftRequest.id);
    setInput(current=>current.trim()?current+'\n\n'+draftRequest.text:draftRequest.text);
  }
  useEffect(()=>{
    if(!draftRequest)return;
    textareaRef.current?.focus();
  },[draftRequest]);
  function removeFiles(files:File[]){
    setPending(current=>current.filter(item=>{
      if(!files.includes(item.file))return true;
      if(item.preview){URL.revokeObjectURL(item.preview);previewUrls.current.delete(item.preview);}
      return false;
    }));
  }
  const addFiles=useCallback((files:File[])=>{
    if(loading||disabled||submitting.current)return;
    const all=[...pending.map(item=>item.file),...files];
    let error='';
    if(all.length>5)error='每次最多添加 5 个文件。';
    else if(files.some(file=>!/\.(csv|xlsx|png|jpe?g|webp)$/i.test(file.name)))error='支持 CSV、XLSX、PNG、JPEG、WebP。';
    else if(files.some(file=>file.size>10*1024*1024))error='每个文件不能超过 10 MB。';
    else if(all.filter(isImage).reduce((sum,file)=>sum+file.size,0)>20*1024*1024)error='单次图片总大小不能超过 20 MB。';
    setFileError(error);if(error)return;
    setPending([...pending,...files.map(file=>{
      const preview=isImage(file)?URL.createObjectURL(file):undefined;
      if(preview)previewUrls.current.add(preview);
      return {file,preview};
    })]);
  },[pending,loading,disabled]);
  useEffect(()=>()=>{previewUrls.current.forEach(url=>URL.revokeObjectURL(url));},[]);
  useEffect(()=>{
    if(lastRevision.current===revision)return;
    lastRevision.current=revision;
    previewUrls.current.forEach(url=>URL.revokeObjectURL(url));previewUrls.current.clear();
    setPending([]);setFileError('');setDragging(false);
  },[revision]); // A new server-assigned session ID must not clear a failed first-send draft.
  useEffect(()=>{
    const over=(event:DragEvent)=>{if(!event.dataTransfer?.types.includes('Files'))return;event.preventDefault();setDragging(!loading&&!disabled);};
    const drop=(event:DragEvent)=>{if(!event.dataTransfer?.types.includes('Files'))return;event.preventDefault();setDragging(false);addFiles(Array.from(event.dataTransfer.files));};
    const leave=(event:DragEvent)=>{if(!event.relatedTarget)setDragging(false);};
    document.addEventListener('dragover',over);document.addEventListener('drop',drop);document.addEventListener('dragleave',leave);
    return()=>{document.removeEventListener('dragover',over);document.removeEventListener('drop',drop);document.removeEventListener('dragleave',leave);};
  },[addFiles,loading,disabled]);
  useEffect(()=>{if(!menuOpen)return;const close=(event:MouseEvent)=>{if(!menuRef.current?.contains(event.target as Node))setMenuOpen(false);};document.addEventListener('mousedown',close);return()=>document.removeEventListener('mousedown',close);},[menuOpen]);

  const handleSubmit = async () => {
    const trimmed = input.trim();
    if ((!trimmed&&!pending.length) || loading || disabled || submitting.current) return;
    if (onConfigureModel && (trimmed||pending.some(item=>isImage(item.file)))) {
      onConfigureModel();
      return;
    }
    const files=pending.map(item=>item.file);
    submitting.current=true;
    try{
      const result=await onSend(trimmed,files);
      if(result&&typeof result==='object')removeFiles(result.uploaded);
      if(result===false||(result&&typeof result==='object'&&!result.sent))return;
      removeFiles(files);setFileError('');
      setInput(current=>current.trim()===trimmed?'':current);
      if (textareaRef.current && textareaRef.current.value.trim()===trimmed) textareaRef.current.style.height = "auto";
    }catch(error){setFileError(error instanceof Error?error.message:'发送失败，附件和问题已保留。');}
    finally{submitting.current=false;}
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
    // Shift+Enter: default behavior (newline) — trigger resize after
    if (e.key === "Enter" && e.shiftKey) {
      setTimeout(() => {
        if (textareaRef.current) {
          textareaRef.current.style.height = "auto";
          textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 200) + "px";
        }
      }, 0);
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length > 0 && !loading && !disabled) {
      addFiles(files);
    }
    if (fileRef.current) fileRef.current.value = "";
  };

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    const el = e.target;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  };

  return (
    <div className="chat-input-shell">
      <div className="chat-input-inner">
        {dragging&&<div className="file-drop-hint" role="status">松开以添加文件或截图</div>}
        {pending.length>0&&<div className="pending-files" aria-label="待发送文件">{pending.map((item,index)=><div className="pending-file" key={index}>
          {item.preview?<a href={item.preview} target="_blank" rel="noreferrer" title={`查看原图 ${item.file.name}`}><Image unoptimized src={item.preview} alt={item.file.name} width={120} height={90}/></a>:<Upload size={20}/>}
          <span title={item.file.name}>{item.file.name}<small>{Math.max(1,Math.round(item.file.size/1024))} KB</small></span>
          <button type="button" disabled={loading} aria-label={`移除文件 ${item.file.name}`} onClick={()=>removeFiles([item.file])}><X size={14}/></button>
        </div>)}</div>}
        {fileError&&<p role="alert" className="text-xs text-destructive">{fileError}</p>}
        {(quote||panel||conversation||charts)&&<div className="composer-attachments">
          {quote&&<div className="composer-attachment quote-attachment"><Quote size={14}/><details><summary>{quote.text.slice(0,90)}{quote.text.length>90?'…':''}</summary><blockquote>{quote.text}</blockquote></details><button aria-label="移除引用" onClick={onRemoveQuote}><X size={13}/></button></div>}
          {panel&&<div className="composer-attachment"><LayoutDashboard size={15}/><span><strong>{panel.title}</strong><small>{panel.chart_count} 张图表 · {panel.note_count} 条笔记 · 发送时读取此版本</small></span><button aria-label="移除面板附件" onClick={onRemovePanel}><X size={13}/></button></div>}
          {conversation&&<div className="composer-attachment"><MessageSquare size={15}/><span><strong>{conversation.title}</strong><small>整场聊天上下文 · 短聊天直接引用，长聊天压缩</small></span><button aria-label="移除聊天上下文" onClick={onRemoveConversation}><X size={13}/></button></div>}
          {charts&&<div className="composer-attachment"><ChartNoAxesCombined size={15}/><span><strong>当前聊天的图表</strong><small>{charts.message_ids.length} 张图表 · 使用已保存的数据</small></span><button aria-label="移除图表附件" onClick={onRemoveCharts}><X size={13}/></button></div>}
        </div>}
        <div className="chat-input-box">
          {/* Upload button */}
          <input
            ref={fileRef}
            type="file"
            aria-label="选择文件或截图"
            multiple
            accept=".csv,.xlsx,.png,.jpg,.jpeg,.webp"
            disabled={loading || disabled}
            className="hidden"
            onChange={handleFileChange}
          />
          <div ref={menuRef} className="composer-add-menu" onKeyDown={event=>{if(event.key==='Escape')setMenuOpen(false);}}><button
            onClick={() => setMenuOpen(!menuOpen)}
            disabled={loading || disabled}
            className="flex-shrink-0 p-1.5 rounded-full hover:bg-muted transition-colors text-muted-foreground hover:text-foreground"
            aria-label="添加附件" aria-haspopup="menu" aria-expanded={Boolean(menuOpen&&!loading&&!disabled)}
            title="添加文件、聊天上下文、图表或面板"
          >
            <Plus className="w-5 h-5" />
          </button>{menuOpen&&!loading&&!disabled&&<div className="composer-add-popover" role="menu" aria-label="添加到问题">
            <button role="menuitem" onClick={()=>{setMenuOpen(false);fileRef.current?.click();}}><Upload size={15}/><span>添加文件或截图<small>CSV / Excel / PNG / JPEG / WebP</small></span></button>
            {onPickConversation&&<button role="menuitem" onClick={()=>{setMenuOpen(false);onPickConversation();}}><MessageSquare size={15}/><span>其他聊天的上下文<small>带入整场聊天的压缩摘要</small></span></button>}
            {onPickCharts&&<button role="menuitem" disabled={!hasCharts} onClick={()=>{setMenuOpen(false);onPickCharts();}}><ChartNoAxesCombined size={15}/><span>当前聊天的图表<small>{hasCharts?'选择已分析的图表':'当前还没有已保存图表'}</small></span></button>}
            {onPickPanel&&<button role="menuitem" onClick={()=>{setMenuOpen(false);onPickPanel();}}><LayoutDashboard size={15}/><span>分析面板<small>图表与笔记一起分析</small></span></button>}
          </div>}</div>

          {/* Text input — never disabled during generation */}
          <textarea
            ref={textareaRef}
            value={input}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            onPaste={event=>{
              const images=Array.from(event.clipboardData.items).filter(item=>item.kind==='file'&&item.type.startsWith('image/')).map(item=>item.getAsFile()).filter((file):file is File=>file!==null);
              if(!images.length||loading||disabled)return;
              event.preventDefault();addFiles(images);
            }}
            placeholder="查指标、追查变化，或告诉我需要怎样清洗和绘图…"
            aria-label="数据问题"
            className="flex-1 bg-transparent border-0 outline-none resize-none text-sm px-3 py-1.5 min-h-[36px] max-h-[200px] placeholder:text-muted-foreground"
            rows={1}
          />

          {/* Send or Stop button */}
          {loading ? (
            <button
              onClick={onStop}
              disabled={!onStop}
              className="send-button"
              aria-label="停止生成"
              title="停止生成"
            >
              <Square className="w-4 h-4 fill-current" />
            </button>
          ) : (
            <button
              onClick={handleSubmit}
              disabled={disabled || (!input.trim()&&!pending.length)}
              className="send-button"
              aria-label={onConfigureModel ? "配置模型后发送" : "发送消息"}
              title={onConfigureModel ? "先配置模型，问题会保留" : "发送消息"}
            >
              <Send className="w-4 h-4" />
            </button>
          )}
        </div>
        <div className="input-footnote"><span>拖入文件 / 粘贴截图 · 最多 5 个 · 每个 10 MB</span><span className="composer-meta"><span>Enter 发送 · Shift + Enter 换行</span>{contextMeter}</span></div>
      </div>
    </div>
  );
}
