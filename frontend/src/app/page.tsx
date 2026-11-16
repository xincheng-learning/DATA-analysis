"use client";

import { useState, useEffect, useRef, useSyncExternalStore } from "react";
import {Dialog} from '@base-ui/react/dialog';
import { Database, PanelLeft, PanelRight, Settings2, Layers3, X, ChevronDown, ScanLine } from "lucide-react";
import {AnalysisStart} from '@/components/analysis-start';
import { useAuth } from "@/components/auth-provider";
import { LoginPage } from "@/components/login-page";
import { Sidebar } from "@/components/sidebar";
import { ChatMessage } from "@/components/chat-message";
import { ChatInput } from "@/components/chat-input";
import { ModelSettings } from "@/components/model-settings";
import {ThinkingControl} from '@/components/thinking-control';
import {AdvancedViews} from '@/components/advanced-views';
import { StorageSettings } from "@/components/storage-settings";
import { DatabaseSettings } from "@/components/database-settings";
import { DataPanel } from "@/components/data-panel";
import { DataWorkbench } from "@/components/data-workbench";
import { AnalysisWorkspace } from "@/components/analysis-workspace";
import { SessionUsage } from "@/components/session-usage";
import {AnalysisPanels} from '@/components/analysis-panels';
import {PanelPicker} from '@/components/panel-picker';
import {QuoteSelection} from '@/components/quote-selection';
import {ContextPicker} from '@/components/context-picker';
import {ContextMeter} from '@/components/context-meter';
import {QuestionNavigator} from '@/components/question-navigator';
import {addBoardChart} from '@/lib/boards';
import type {QuoteReference,PanelReference,ContextReference,ConversationReference,ImageReference,ChatAttachments} from '@/lib/api';
import { appendTaskResult, reconcileTaskHistory, reportSources,withScopeHistory } from "@/lib/analysis";
import { isLocalMode } from "@/lib/supabase";
import {
  askQuestion,
  listSessions,
  getSession,
  deleteSession,
  renameSession,
  uploadFiles,
  getModelSettings,
  getSessionTables, startAnalysis, readAnalysis, streamAnalysis, cancelAnalysis, importDatabase, executeSqlProposal, continueAnalysis,
  type AnalysisTask, type DataTable, type DatabaseTable,
  type ModelSettingsStatus,
  type SessionListItem,
  type Message,
} from "@/lib/api";

interface LocalMessage {
  id?:number|string|null;
  role: "user" | "assistant";
  content: string;
  metadata: Record<string, unknown>;
}

const subscribeStoredTask=(notify:()=>void)=>{window.addEventListener('storage',notify);return()=>window.removeEventListener('storage',notify);};
const storedTask=()=>isLocalMode?sessionStorage.getItem('datapilot-task'):null;
const noStoredTask=()=>null;

export default function ChatPage() {
  const { user, loading: authLoading, signOut } = useAuth();
  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [busy, setLoading] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [storageOpen, setStorageOpen] = useState(false);
  const [modelStatus, setModelStatus] = useState<ModelSettingsStatus | null>(null);
  const [modelStatusError, setModelStatusError] = useState(false);
  const [thinkingBusy,setThinkingBusy]=useState(false);
  const [advancedOpen,setAdvancedOpen]=useState(false),[advancedId,setAdvancedId]=useState<string>();
  const [databaseOpen, setDatabaseOpen] = useState(false);
  const [tables, setTables] = useState<DataTable[]>([]);
  const [cleaningRevision,setCleaningRevision]=useState(0);
  const [dataError, setDataError] = useState("");
  const [cleaningOpen, setCleaningOpen] = useState(false);
  const [workbenchTab,setWorkbenchTab]=useState<'cleaning'|'relationships'>('cleaning');
  const [progress, setProgress] = useState<AnalysisTask | null>(null);
  const savedTaskId=useSyncExternalStore(subscribeStoredTask,storedTask,noStoredTask);
  const [taskId, setTaskId] = useState<string | null>();
  const runningTaskId=taskId===undefined?savedTaskId:taskId;
  const loading=busy||(taskId===undefined&&!!savedTaskId);
  const [trackingError, setTrackingError] = useState("");
  const [historySyncTask, setHistorySyncTask] = useState<AnalysisTask | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historySyncError, setHistorySyncError] = useState("");
  const historyRequest = useRef(0);
  const restoreTaskHistory = useRef(false);
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [draftRequest,setDraftRequest]=useState<{text:string;id:number}|null>(null);
  const [dataOpen,setDataOpen]=useState(false);
  const [workspaceWidth, setWorkspaceWidth] = useState(600);
  const [selection, setSelection] = useState<number | null>(null);
  const [sourceSelection,setSourceSelection]=useState<string|null>(null);
  const [mobileView, setMobileView] = useState<"chat" | "workspace">("chat");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [panelsOpen,setPanelsOpen]=useState(false);
  const [panelsVisited,setPanelsVisited]=useState(false);
  const [panelId,setPanelId]=useState<string|undefined>();
  const [panelPicker,setPanelPicker]=useState<'add'|'attach'|null>(null);
  const [chartSource,setChartSource]=useState<{sid:string;mid:number;sourcePath?:(string|number)[]}|null>(null);
  const [quote,setQuote]=useState<QuoteReference|null>(null);
  const [attachedPanel,setAttachedPanel]=useState<PanelReference|null>(null);
  const [conversation,setConversation]=useState<ConversationReference|null>(null),[attachedCharts,setAttachedCharts]=useState<ContextReference|null>(null);
  const [contextPicker,setContextPicker]=useState<'conversation'|'charts'|null>(null);
  const [notice,setNotice]=useState('');
  const [composerRevision,setComposerRevision]=useState(0);
  const savePanels=useRef<()=>Promise<void>>(async()=>{});
  const bodyRef = useRef<HTMLDivElement>(null);
  const resizeStart = useRef<{ x: number; width: number } | null>(null);
  const stopRequested = useRef(false);
  const conversationScroll=useRef<HTMLDivElement>(null);
  const followLatest=useRef(true);
  const abortControllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!isLocalMode) return;
    getModelSettings().then(setModelStatus).catch(() => setModelStatusError(true));
  }, []);

  useEffect(() => {
    if (!activeSessionId) return;
    let active = true;
    getSessionTables(activeSessionId).then((data) => { if (active) { setTables(data); setDataError(""); } }).catch((error) => { if (active) setDataError(error.message); });
    return () => { active = false; };
  }, [activeSessionId]);

  useEffect(() => {
    if (!runningTaskId) return;
    const id = runningTaskId, controller = new AbortController();
    if(taskId===undefined)restoreTaskHistory.current=true;
    let active = true, finished = false;
    async function acceptTask(task:AnalysisTask) {
      if (!active) return;
      setTrackingError("");
      finished = ["succeeded", "failed", "cancelled"].includes(task.status);
      if (restoreTaskHistory.current) {
        restoreTaskHistory.current = false;
        setTables([]);setDataError('');setActiveSessionId(task.session_id);
        if (!finished) {
          const history = await getSession(task.session_id).catch(() => null);
          if (!active) return;
          if (history) setMessages(history.messages);
        }
      }
      setProgress(task);
      if (!finished) return;
      const detail = await getSession(task.session_id).catch(() => null);
      if (!active) return;
      if(task.result?.cleaning_event){
        const updated=await getSessionTables(task.session_id).catch(()=>null);
        if(!active)return;
        if(updated){setTables(updated);setCleaningRevision(value=>value+1);setDataError('');}
        else setDataError('数据处理已完成，表概览读取失败；重新打开聊天可重试。');
      }
      if (detail) applyTaskHistory(detail.messages, task);
      else {
        setMessages(previous => appendTaskResult(previous, task));
        setHistorySyncTask(task);setHistorySyncError("");
      }
      if(task.result?.analysis_report&&reportSources(task.result as unknown as Record<string,unknown>).some(source=>source.hasChart))setWorkspaceOpen(true);
      setLoading(false);setProgress(null);setTaskId(null);void loadSessions();
    }
    async function follow() {
      while(active&&!finished){
        try { await streamAnalysis(id,acceptTask,controller.signal); }
        catch {
          if(!active||controller.signal.aborted)return;
          try { await acceptTask(await readAnalysis(id)); }
          catch(error) {
            if(!active)return;
            if((error as {status?:number}).status===404){
              sessionStorage.removeItem('datapilot-task');setLoading(false);setProgress(null);setTaskId(null);
              setTrackingError('任务记录已不存在，后端可能已重启。请查看会话中已保存的结果；未返回的用量仍无法确认。');return;
            }
          }
          if(active&&!finished){
            setTrackingError('流式连接暂时中断，正在恢复；已收到的内容保留，停止按钮继续可用。');
            await new Promise(resolve=>setTimeout(resolve,1200));
          }
        }
      }
    }
    void follow();
    return () => {active=false;controller.abort();};
  }, [runningTaskId,taskId]);

  function openPanels(){setPanelsVisited(true);setPanelsOpen(true);}

  function applyTaskHistory(history: LocalMessage[], task: AnalysisTask) {
    const restored = reconcileTaskHistory(history, task);
    setMessages(restored.messages);
    if (restored.saved) {
      sessionStorage.removeItem('datapilot-task');
      setHistorySyncTask(null);
    } else {
      setHistorySyncTask(task);
      setHistorySyncError(task.error || '历史中尚未找到本轮结果，已保留任务返回的答案和用量。');
    }
  }

  // Load sessions when user is authenticated
  useEffect(() => {
    if (user || isLocalMode) {
      loadSessions();
    }
  }, [user]);

  // Keep the reader's position when they have moved away from the latest reply.
  useEffect(() => {
    if(followLatest.current)conversationScroll.current?.scrollTo({top:conversationScroll.current.scrollHeight,behavior:'instant'});
  }, [messages,progress?.draft_answer]);

  async function loadSessions(throwOnError=false) {
    try {
      const list = await listSessions();
      setSessions(list);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '聊天列表读取失败，请刷新页面重试。');
      if(throwOnError)throw error;
    }
  }

  async function handleSelectSession(sid: string) {
    if (loading) return;
    setComposerRevision(value=>value+1);
    followLatest.current=true;
    setPanelsOpen(false);setQuote(null);setAttachedPanel(null);setConversation(null);setAttachedCharts(null);
    const request = ++historyRequest.current;
    sessionStorage.removeItem("datapilot-task");
    setHistorySyncTask(null); setTrackingError(""); setHistoryLoading(true);
    setSelection(null);setSourceSelection(null); setSidebarOpen(false);
    setWorkspaceOpen(false);setMobileView('chat');setDataOpen(false);
    setTables([]);setDataError('');
    setActiveSessionId(sid);
    setMessages([]);
    try {
      const detail = await getSession(sid);
      if (request !== historyRequest.current) return;
      setMessages(
        detail.messages.map((m: Message) => ({
          role: m.role,
          content: m.content,
          metadata: m.metadata || {},id:m.id,
        }))
      );
      const latest=detail.messages.at(-1);
      if(latest&&reportSources(latest.metadata||{}).some(source=>source.hasChart))setWorkspaceOpen(true);
    } catch (err) {
      if (request !== historyRequest.current) return;
      setMessages([
        {
          role: "assistant",
          content: `无法加载聊天记录：${
            err instanceof TypeError ? "无法连接后端，请确认后端服务已启动。" : err instanceof Error ? err.message : "请确认后端服务已启动。"
          }`,
          metadata: {},
        },
      ]);
    } finally { if (request === historyRequest.current) setHistoryLoading(false); }
  }

  function handleNewChat() {
    if (loading) return;
    setComposerRevision(value=>value+1);
    followLatest.current=true;
    setPanelsOpen(false);setQuote(null);setAttachedPanel(null);setConversation(null);setAttachedCharts(null);
    historyRequest.current++;
    sessionStorage.removeItem("datapilot-task");
    setHistoryLoading(false); setHistorySyncTask(null); setTrackingError("");
    setSelection(null);setSourceSelection(null); setSidebarOpen(false);
    setWorkspaceOpen(false);setMobileView('chat');setDataOpen(false);
    setTables([]);setDataError('');
    setActiveSessionId(null);
    setMessages([]);
  }

  async function handleDeleteSession(sid: string) {
    if (loading) throw new Error('请等待当前分析结束后再删除。');
    const request = historyRequest.current;
    await deleteSession(sid);
    if (request === historyRequest.current && activeSessionId === sid) handleNewChat();
    setSessions(items=>items.filter(item=>item.session_id!==sid));
  }

  async function handleRenameSession(sid: string, title: string) {
    await renameSession(sid, title);
    await loadSessions();
  }

  async function handleSend(question: string, files:File[] = [], extra:Pick<ChatAttachments,'clarification_reply'|'cleaning_reply'> = {}) {
    if (loading || historyLoading || thinkingBusy) return false;
    const reply=extra.cleaning_reply||extra.clarification_reply;
    const source=reply?messages.find(message=>message.id===reply.message_id):undefined;
    const requestGoal=String((source?.metadata.workflow as {goal?:string}|undefined)?.goal||(source?.metadata.clarification as {goal?:string}|undefined)?.goal||question);
    followLatest.current=true;
    historyRequest.current++;
    sessionStorage.removeItem("datapilot-task");
    setHistorySyncTask(null); setTrackingError("");
    setSelection(null);setSourceSelection(null);
    let sid=activeSessionId;
    let userMsg:LocalMessage|undefined;
    const uploaded:File[]=[];
    const images:ImageReference[]=[];
    setLoading(true);
    stopRequested.current = false;

    // Create abort controller for this request
    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      await savePanels.current();
      if(files.length){
        sid ||= crypto.randomUUID().replace(/-/g,'');
        setActiveSessionId(sid);
        const result=await uploadFiles(sid,files);
        const imported=result.uploaded.filter(item=>!item.image);
        uploaded.push(...imported.map(item=>files[item.upload_index]));
        images.push(...result.uploaded.flatMap(item=>item.image?[item.image]:[]));
        if(imported.length)setMessages(previous=>[...previous,{role:'user',content:`📎 已上传：${imported.map(item=>item.filename).join(', ')}`,metadata:{type:'upload',files:imported}}]);
        if(result.errors.length)setMessages(previous=>[...previous,{role:'assistant',content:`以下文件未能上传：\n${result.errors.map(item=>`• ${item.filename}：${item.error}`).join('\n')}`,metadata:{}}]);
        await loadSessions();
        await getSessionTables(sid).then(data=>{setTables(data);setDataError('');}).catch(()=>setDataError('文件已接收，表概览读取失败；重新打开聊天可重试。'));
        if(result.errors.length||result.uploaded.length<files.length||stopRequested.current)return {sent:false,uploaded};
        if(!question&&!images.length)return {sent:true,uploaded};
      }
      if(stopRequested.current)return {sent:false,uploaded};
      question ||= '请识别这些截图中的内容；如果包含 SQL 或报错，请指出问题、解释原因并给出修改建议。';
      const confirming=!!(extra.clarification_reply||extra.cleaning_reply);
      const context={...(!confirming?{...(quote?{quote}:{}),...(attachedPanel?{board:attachedPanel}:{}),...(conversation?{conversation}:{}),...(attachedCharts?{charts:attachedCharts}:{})}:{}),...(images.length?{images}:{}),...extra};
      userMsg={role:'user',content:question,metadata:{context_snapshot:context}};
      setMessages(previous=>[...previous,userMsg!]);
      if (isLocalMode) {
        const task = await startAnalysis(question, sid || undefined, 'quick',{...context,...(!confirming&&attachedPanel?{board:{id:attachedPanel.id,version:attachedPanel.version}}:{})});
        sessionStorage.setItem("datapilot-task", task.task_id);
        setTaskId(task.task_id);
        setActiveSessionId(task.session_id);
        setProgress(task);
        if (stopRequested.current) {
          try { await requestTaskStop(task.task_id); }
          catch { setTrackingError('任务已接收，停止请求未确认，请再次点击停止。'); }
        }
        if(!confirming){setQuote(null);setAttachedPanel(null);setConversation(null);setAttachedCharts(null);}return true;
      }
      const response = await askQuestion(question, sid || undefined, controller.signal);

      // Set session if new
      if (!activeSessionId) {
        setActiveSessionId(response.session_id);
      }

      // Add assistant message
      const assistantMsg: LocalMessage = {
        role: "assistant",
        content: response.answer,
        metadata: { ...response },
      };
      setMessages((prev) => [...prev, assistantMsg]);

      // Refresh session list
      await loadSessions();
      setQuote(null);setAttachedPanel(null);return true;
    } catch (err) {
      setMessages(previous=>previous.filter(message=>message!==userMsg));
      if (err instanceof Error && err.name === "AbortError") {
        // User cancelled — add a note
        const cancelMsg: LocalMessage = {
          role: "assistant",
          content: "已停止生成。",
          metadata: {task_status:'cancelled',request_question:requestGoal},
        };
        setMessages((prev) => [...prev, cancelMsg]);
      } else {
        const errorMsg: LocalMessage = {
          role: "assistant",
          content: err instanceof Error && err.message.includes("401")
            ? "登录已过期，请重新登录。"
            : err instanceof TypeError ? "无法连接后端，请确认后端服务已启动。"
            : err instanceof Error ? err.message : "处理失败，请重试或换一种方式提问。",
          metadata: {error:err instanceof Error?err.message:'请求未完成',request_question:requestGoal},
        };
        setMessages((prev) => [...prev, errorMsg]);
      }
      return {sent:false,uploaded};
    } finally {
      if (!isLocalMode || !sessionStorage.getItem("datapilot-task")) setLoading(false);
      abortControllerRef.current = null;
    }
  }

  async function requestTaskStop(id: string) {
    const task = await cancelAnalysis(id);
    setProgress((current) => current?.task_id === id && !["succeeded", "failed", "cancelled"].includes(current.status) ? task : current);
  }

  async function handleExecuteSql(mid:number,resume=false){
    if(loading||historyLoading||!activeSessionId)return false;
    setLoading(true);setTrackingError('');setHistorySyncTask(null);setSelection(null);historyRequest.current++;stopRequested.current=false;
    try{
      const task=await (resume?continueAnalysis:executeSqlProposal)(activeSessionId,mid);
      sessionStorage.setItem('datapilot-task',task.task_id);setTaskId(task.task_id);setProgress(task);
      if(stopRequested.current){try{await requestTaskStop(task.task_id);}catch{setTrackingError('停止请求未确认，请再次点击停止。');}}
      return true;
    }catch(error){setLoading(false);setNotice(error instanceof Error?error.message:resume?'未能继续任务，已有结果保留。':'未能开始只读验证。');return false;}
  }

  async function handleStop() {
    if (isLocalMode) {
      stopRequested.current = true;
      if (runningTaskId) {
        try { await requestTaskStop(runningTaskId); }
        catch (error) { setDataError(error instanceof Error ? error.message : "停止请求未确认，请稍后重试。"); }
      }
      return;
    }
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
  }

  async function handleDatabaseImport(selected: DatabaseTable[]) {
    const sid = activeSessionId || crypto.randomUUID().replace(/-/g, "");
    await importDatabase(sid, selected);
    setActiveSessionId(sid);
    setTables(await getSessionTables(sid)); setDataError("");
    const detail = await getSession(sid); setMessages(detail.messages);
    await loadSessions();
  }

  async function retryHistorySync() {
    if (!historySyncTask || historyLoading) return;
    const request = ++historyRequest.current;
    setHistoryLoading(true); setHistorySyncError("");
    try {
      const detail = await getSession(historySyncTask.session_id);
      if (request !== historyRequest.current) return;
      applyTaskHistory(detail.messages, historySyncTask);
    } catch {
      if (request === historyRequest.current) setHistorySyncError("历史同步仍未成功，请稍后重试，当前结果和用量已保留。");
    } finally { if (request === historyRequest.current) setHistoryLoading(false); }
  }

  function resizeWorkspace(width: number) {
    setWorkspaceWidth(Math.max(340, Math.min(width, 1400, (bodyRef.current?.clientWidth || 1000) - 340)));
  }

  function viewChart(index: number,sourceKey?:string) {
    setSelection(index);setSourceSelection(sourceKey||null); setWorkspaceOpen(true); setMobileView("workspace");
    requestAnimationFrame(()=>document.querySelector('.workspace-container .workspace-scroll')?.scrollTo({top:0,behavior:'smooth'}));
  }

  function jumpToMessage(index:number){
    followLatest.current=false;setMobileView('chat');
    requestAnimationFrame(()=>{
      const target=document.getElementById(`message-${index}`),scroll=conversationScroll.current;
      if(!target||!scroll)return;
      scroll.scrollTo({top:scroll.scrollTop+target.getBoundingClientRect().top-scroll.getBoundingClientRect().top-24,behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
      target.focus({preventScroll:true});
    });
  }

  function jumpToLatest(){
    followLatest.current=true;
    conversationScroll.current?.scrollTo({top:conversationScroll.current.scrollHeight,behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});
  }

  const displayMessages=withScopeHistory(messages,loading?progress?.result?.analysis_scope:undefined);
  return (
    <>
      {authLoading ? (
        <div className="flex h-screen items-center justify-center">
          <p className="text-muted-foreground">加载中…</p>
        </div>
      ) : !user && !isLocalMode ? (
        <LoginPage />
      ) : (
    <div className="app-shell">
      {/* Sidebar — fixed, independent scroll */}
      {sidebarOpen && <button className="sidebar-scrim" aria-label="关闭会话列表" onClick={() => setSidebarOpen(false)} />}
      <div className="session-sidebar" data-open={sidebarOpen}><Sidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        onNewChat={handleNewChat}
        onSelectSession={handleSelectSession}
        onDeleteSession={handleDeleteSession}
        onRenameSession={handleRenameSession}
        onRefresh={()=>loadSessions(true)}
        onSignOut={isLocalMode ? undefined : signOut}
        userEmail={isLocalMode ? "本地工作区" : user?.email}
        onModelSettings={isLocalMode ? () => setSettingsOpen(true) : undefined}
        onStorageSettings={isLocalMode ? () => setStorageOpen(true) : undefined}
        settingsDisabled={loading}
        onPanels={isLocalMode?()=>{openPanels();setSidebarOpen(false);}:undefined} panelsActive={panelsOpen}
      /></div>

      {/* Main chat area — flex column, own scroll */}
      {(panelsOpen||panelsVisited)&&<AnalysisPanels active={panelsOpen} initialId={panelId} onSaveReady={save=>{savePanels.current=save;}} onClose={()=>setPanelsOpen(false)} onAnalyse={panel=>{setAttachedPanel(panel);setPanelsOpen(false);setMobileView('chat');}}/>}
      <main className="workbench" style={panelsOpen?{display:'none'}:undefined}>
        <header className="workbench-header"><div className="workbench-title"><button className="icon-button sidebar-toggle" aria-label="打开会话列表" onClick={() => setSidebarOpen(true)}><PanelLeft size={18} /></button><div><h1>{sessions.find((session) => session.session_id === activeSessionId)?.title || "新的分析"}</h1></div></div>
          <div className="header-actions"><SessionUsage messages={messages} progress={progress} /><button className={`toolbar-button workspace-toggle ${workspaceOpen ? "is-active" : ""}`} aria-label={workspaceOpen ? "收起分析工作区" : "打开分析工作区"} aria-pressed={workspaceOpen} onClick={() => { setWorkspaceOpen(!workspaceOpen); setMobileView(workspaceOpen ? "chat" : "workspace"); }}><PanelRight size={17} /><span>图表与依据</span></button></div>
        </header>
        <div className="workbench-toolbar"><div className="toolbar-primary">{isLocalMode && <ThinkingControl status={modelStatus} disabled={loading} onSaved={setModelStatus} onBusy={setThinkingBusy}/>}
          <button className="toolbar-button" aria-label="查看当前数据" onClick={()=>setDataOpen(true)}><Database size={14}/>数据{tables.length>0&&<span className="toolbar-count">{tables.length}</span>}</button>
          {isLocalMode&&<button className="toolbar-button" disabled={loading||historyLoading||!activeSessionId||!tables.length} title={tables.length?'查看体检、规则与处理前后对照':'先导入当前聊天的数据'} onClick={()=>{setWorkbenchTab('cleaning');setCleaningOpen(true);}}><ScanLine size={14}/>体检与清洗</button>}
          {isLocalMode&&<button className="toolbar-button" disabled={loading||historyLoading||!activeSessionId||!tables.length} aria-label="数据结构与关系" onClick={()=>{setWorkbenchTab('relationships');setCleaningOpen(true);}}><Database size={14}/>数据结构</button>}
          {isLocalMode&&<details className="results-menu"><summary className="toolbar-button"><Layers3 size={14}/>成果<ChevronDown size={12}/></summary><div><button disabled={loading} onClick={event=>{event.currentTarget.closest('details')?.removeAttribute('open');openPanels();}}>分析面板<span>组合图表与结论</span></button><button disabled={loading} onClick={event=>{event.currentTarget.closest('details')?.removeAttribute('open');setAdvancedId(undefined);setAdvancedOpen(true);}}>高级可视化<span>树图、流向与透视 · Superset</span></button></div></details>}
        </div>
          {isLocalMode && <button className="model-status" onClick={() => setSettingsOpen(true)} disabled={loading||thinkingBusy} title={modelStatus?.thinking_supported ? `思考模式：${{ auto: "自动兼容", enabled: "开启", disabled: "关闭" }[modelStatus.thinking_mode || "auto"]}` : "模型设置"}><span className={modelStatus?.configured ? "status-dot configured" : "status-dot"} />{modelStatusError ? "模型状态读取失败" : !modelStatus ? "读取配置中" : modelStatus.configured ? modelStatus.model : "配置模型"}<Settings2 size={12} /></button>}
        </div>
        {workspaceOpen&&<div className="mobile-pane-tabs"><button aria-pressed={mobileView === "chat"} onClick={() => setMobileView("chat")}>对话分析</button><button aria-pressed={mobileView === "workspace"} onClick={() => { setWorkspaceOpen(true); setMobileView("workspace"); }}>图表与依据</button></div>}
        <div className="workbench-body" ref={bodyRef} data-view={mobileView}>
        <div className="conversation-pane">

        {cleaningOpen && activeSessionId && <DataWorkbench key={`${activeSessionId}:${workbenchTab}`} initialTab={workbenchTab} sessionId={activeSessionId} disabled={loading} onClose={() => setCleaningOpen(false)} onTaskStarted={task=>{setTaskId(task.task_id);setProgress(task);setLoading(true);}} onChanged={updated => { setTables(updated);setCleaningRevision(value=>value+1); setDataError(''); }} />}
        {dataError&&<p role="alert" className="data-load-error">{dataError}<button onClick={()=>setDataOpen(true)}>查看数据</button></p>}
        {trackingError && <div role="alert" className="mx-5 my-2 rounded border border-amber-300/40 bg-amber-50/50 p-3 text-xs text-amber-800 dark:bg-amber-950/20 dark:text-amber-200">{trackingError}</div>}
        {historySyncTask && <div role="status" className="mx-5 my-2 rounded border p-3 text-xs text-muted-foreground">{historySyncError || "聊天历史暂时无法读取，已恢复任务返回的结果和用量。"}<button className="ml-2 text-primary underline disabled:opacity-50" disabled={historyLoading} onClick={retryHistorySync}>{historyLoading ? "同步中…" : "重试同步历史"}</button></div>}
        {/* Messages — scrollable independently */}
        <div className="conversation-scroll-shell">
        <QuestionNavigator key={activeSessionId||'new'} messages={messages} onJump={jumpToMessage} onLatest={jumpToLatest}/>
        <div ref={conversationScroll} className="conversation-scroll" aria-label="聊天记录" tabIndex={0} onScroll={event=>{const scroll=event.currentTarget;followLatest.current=scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<80;}}>
          <div className="conversation-content">
            {historyLoading && messages.length === 0 ? <div role="status" className="p-6 text-sm text-muted-foreground">正在读取会话…</div> : messages.length === 0 ? (
              <AnalysisStart disabled={loading||historyLoading||thinkingBusy} onChoose={text=>setDraftRequest(previous=>({text,id:(previous?.id||0)+1}))}/>
            ) : (
              <>
                {displayMessages.map((msg, i) => (
                  <div id={`message-${i}`} key={`${activeSessionId || "new"}:${i}`} className="message-anchor" tabIndex={-1} data-quote-message={msg.role==='assistant'&&typeof msg.id==='number'?msg.id:undefined}><ChatMessage
                    role={msg.role}
                    content={msg.content}
                    metadata={msg.metadata}
                    sessionId={activeSessionId || undefined}
                    cleaningDisabled={loading||historyLoading}
                    cleaningRevision={messages.length+cleaningRevision}
                    onClarificationAnswer={isLocalMode&&typeof msg.id==='number'&&msg.id===[...messages].reverse().find(message=>message.role==='assistant')?.id?async (answer,answers)=>(await handleSend(answer,[],{clarification_reply:{message_id:Number(msg.id),...(answers?{answers}:{})}}))===true:undefined}
                    clarificationAnswer={messages.find(message=>message.role==='user'&&(message.metadata.clarification_reply as {message_id?:number}|undefined)?.message_id===msg.id)?.content}
                    onConfirmCleaning={isLocalMode&&typeof msg.id==='number'&&msg.id===[...messages].reverse().find(message=>message.role==='assistant')?.id?async planId=>(await handleSend('确认清洗并继续原任务',[],{cleaning_reply:{message_id:Number(msg.id),plan_id:planId}}))===true:undefined}
                    onFollowUp={isLocalMode&&i===messages.findLastIndex(message=>message.role==='assistant')?text=>setDraftRequest(previous=>({text,id:(previous?.id||0)+1})):undefined}
                    onResume={isLocalMode&&typeof msg.id==='number'&&i===messages.findLastIndex(message=>message.role==='assistant')?()=>handleExecuteSql(Number(msg.id),true):undefined}
                    originalRequest={String(msg.metadata.request_question||(msg.metadata.workflow as {goal?:string}|undefined)?.goal||(msg.metadata.clarification_context as {goal?:string}|undefined)?.goal||(msg.metadata.analysis_brief as {goal?:string}|undefined)?.goal||messages.slice(0,i).findLast(message=>message.role==='user'&&message.metadata.type!=='upload')?.content||'')}
                    onExecuteSql={isLocalMode&&typeof msg.id==='number'?()=>handleExecuteSql(Number(msg.id)):undefined}
                    proposalExecuted={messages.some(message=>(message.metadata.sql_source as {message_id?:number}|undefined)?.message_id===msg.id&&!message.metadata.error)}
                    onCleaningChanged={async()=>{
                      const sid=activeSessionId,request=historyRequest.current;
                      if(!sid)return;
                      const [detail,data]=await Promise.all([getSession(sid),getSessionTables(sid)]);
                      if(historyRequest.current!==request)return;
                      setMessages(detail.messages);setTables(data);setSelection(null);setCleaningRevision(value=>value+1);
                    }}
                    onViewChart={sourceKey => viewChart(i,sourceKey)}
                    onOpenAdvanced={id=>{setAdvancedId(id);setAdvancedOpen(true);}} onOpenBoard={id=>{setPanelId(id);openPanels();}}
                    onJumpReference={id=>{const index=messages.findIndex(message=>message.id===id);if(index>=0)jumpToMessage(index);}}
                  /></div>
                ))}
                {tables.length>0&&!messages.some(message=>message.role==='user'&&message.metadata.type!=='upload')&&<AnalysisStart hasData disabled={loading||historyLoading||thinkingBusy} onChoose={text=>setDraftRequest(previous=>({text,id:(previous?.id||0)+1}))} onInspect={()=>{setWorkbenchTab('cleaning');setCleaningOpen(true);}} onRelate={()=>{setWorkbenchTab('relationships');setCleaningOpen(true);}}/>}
              </>
            )}
            {progress?.draft_answer&&loading&&<div className="streaming-reply" aria-label="正在生成回复" aria-busy="true"><ChatMessage role="assistant" content={progress.draft_answer} metadata={{streaming:true}}/></div>}
          </div>
        </div>
        </div>

        {/* Input — pinned to bottom, centered when no messages */}
        <div className="conversation-composer">
          {progress && <div role="status" className="task-progress"><span className="progress-pulse" /><span>{progress.status === "cancelling" ? "已请求停止，等待当前步骤结束；已发出的模型请求可能仍计费。" : progress.stage} · {progress.elapsed_seconds} 秒 · 调用 {progress.model_calls}/{progress.max_model_calls}<small>关闭页面不会停止任务 · <button onClick={()=>{setWorkspaceOpen(true);setMobileView('workspace');}}>查看执行过程</button></small></span></div>}
          <ChatInput
            contextMeter={isLocalMode?<ContextMeter sessionId={activeSessionId} revision={messages.length} pending={loading}/>:undefined}
            onSend={handleSend}
            revision={composerRevision}
            draftRequest={draftRequest}
            onStop={handleStop}
            loading={loading}
            disabled={historyLoading||thinkingBusy}
            onConfigureModel={isLocalMode && modelStatus && !modelStatus.configured ? () => setSettingsOpen(true) : undefined}
            quote={quote} panel={attachedPanel} onRemoveQuote={()=>setQuote(null)} onRemovePanel={()=>setAttachedPanel(null)} onPickPanel={isLocalMode?()=>setPanelPicker('attach'):undefined}
            conversation={conversation} charts={attachedCharts} onRemoveConversation={()=>setConversation(null)} onRemoveCharts={()=>setAttachedCharts(null)}
            onPickConversation={isLocalMode?()=>setContextPicker('conversation'):undefined} onPickCharts={isLocalMode?()=>setContextPicker('charts'):undefined} hasCharts={messages.some(message=>typeof message.id==='number'&&Array.isArray((message.metadata.chart_spec as {data?:unknown[]}|undefined)?.data)&&((message.metadata.chart_spec as {data:unknown[]}).data.length>0))}
          />
        </div>
        </div>
        {workspaceOpen && <><div className="workspace-separator" role="separator" aria-label="调整分析工作区宽度" aria-orientation="vertical" aria-valuemin={340} aria-valuemax={1400} aria-valuenow={workspaceWidth} tabIndex={0}
          onPointerDown={(event) => { resizeStart.current = { x: event.clientX, width: workspaceWidth }; event.currentTarget.setPointerCapture(event.pointerId); }}
          onPointerMove={(event) => { if (resizeStart.current) resizeWorkspace(resizeStart.current.width + resizeStart.current.x - event.clientX); }}
          onPointerUp={() => { resizeStart.current = null; }} onPointerCancel={() => { resizeStart.current = null; }}
          onKeyDown={(event) => { if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); resizeWorkspace(workspaceWidth + (event.key === "ArrowLeft" ? 32 : -32)); } }}><span /></div>
          <div className="workspace-container" style={{ width: workspaceWidth }}><AnalysisWorkspace key={activeSessionId || "new"} sessionId={activeSessionId||undefined} messages={displayMessages} progress={progress} pending={loading} selection={selection} sourceSelection={sourceSelection} onSelectSource={setSourceSelection} onSelect={index=>{setSelection(index);setSourceSelection(null);}} onAddChart={isLocalMode?(index,sourcePath)=>{const mid=messages[index]?.id;if(!activeSessionId||typeof mid!=='number'){setNotice('图表还在保存，请稍后重新打开此聊天再加入。');return;}setChartSource({sid:activeSessionId,mid,sourcePath});setPanelPicker('add');}:undefined} onJump={jumpToMessage} /></div></>}
        </div>
      </main>
      {isLocalMode&&<QuoteSelection disabled={panelsOpen||loading||historyLoading} onQuote={value=>{setQuote(value);setMobileView('chat');document.querySelector<HTMLTextAreaElement>('textarea[aria-label="数据问题"]')?.focus();}}/>}
      {panelPicker&&<PanelPicker title={panelPicker==='add'?'将图表加入面板':'添加面板到聊天'} onClose={()=>setPanelPicker(null)} onPick={async panel=>{if(panelPicker==='add'&&chartSource){await savePanels.current();await addBoardChart(panel.id,chartSource.sid,chartSource.mid,chartSource.sourcePath);setPanelId(panel.id);setNotice(`已加入“${panel.title}”的图表素材列。`);}else setAttachedPanel(panel);}}/>}
      {contextPicker&&<ContextPicker key={`${contextPicker}:${activeSessionId}`} mode={contextPicker} currentSessionId={activeSessionId} onClose={()=>setContextPicker(null)} onPick={value=>contextPicker==='conversation'?setConversation(value):setAttachedCharts(value as ContextReference)}/>}
      {notice&&<div className="panel-toast" role="status">{notice}{panelId&&<button onClick={()=>{openPanels();setNotice('');}}>打开面板 ↗</button>}<button aria-label="关闭提示" onClick={()=>setNotice('')}>×</button></div>}
      {isLocalMode && settingsOpen && <ModelSettings open={settingsOpen} onOpenChange={setSettingsOpen} onSaved={(status) => { setModelStatus(status); setModelStatusError(false); }} />}
      {isLocalMode&&advancedOpen&&<AdvancedViews sessionId={activeSessionId} messages={messages} initialId={advancedId} onClose={()=>setAdvancedOpen(false)} onAnalyse={async view=>{setAdvancedOpen(false);await handleSend(`请用工具回读高级视图“${view.title}”（视图ID：${view.id}），核对保存的默认查询后，概括数据结果。`);}}/>}
      {isLocalMode && storageOpen && <StorageSettings open={storageOpen} onOpenChange={setStorageOpen} onSaved={async () => { await loadSessions(); if (activeSessionId) await handleSelectSession(activeSessionId); }} />}
      {isLocalMode && databaseOpen && <DatabaseSettings onClose={() => setDatabaseOpen(false)} onImport={handleDatabaseImport} />}
      <Dialog.Root open={dataOpen} onOpenChange={setDataOpen}><Dialog.Portal><Dialog.Backdrop className="fixed inset-0 z-50 bg-black/40"/><Dialog.Popup className="data-drawer">
        <header><div><Dialog.Title>数据与清洗</Dialog.Title><Dialog.Description>当前聊天的数据、质量与处理记录</Dialog.Description></div><button className="icon-button" aria-label="关闭数据与清洗" onClick={()=>setDataOpen(false)}><X size={18}/></button></header>
        <div className="data-drawer-actions">{isLocalMode&&<><button className="toolbar-button" disabled={loading} onClick={()=>{setDataOpen(false);setDatabaseOpen(true);}}><Database size={14}/>连接数据库</button><button className="toolbar-button" disabled={loading||historyLoading||!activeSessionId||!tables.length} title={!activeSessionId||!tables.length?'请先在当前聊天导入数据，再查看体检与清洗':'查看数据体检、清洗规则和处理前后对照'} aria-label="数据工作台 · 体检与清洗" onClick={()=>{setDataOpen(false);setWorkbenchTab('cleaning');setCleaningOpen(true);}}>体检与清洗</button></>}</div>
        <p>把 CSV、Excel 或截图拖入聊天即可添加。也可以直接告诉 Agent 清洗要求，查看处理前后对照后再确认。</p>
        <DataPanel key={activeSessionId||'new'} tables={tables} error={dataError} expanded/>
        {!tables.length&&!dataError&&<div className="data-drawer-empty"><Database size={28} strokeWidth={1.2}/><strong>还没有导入数据</strong><span>数据只用于当前聊天，文件夹不会共享数据。</span></div>}
      </Dialog.Popup></Dialog.Portal></Dialog.Root>
    </div>
      )}
    </>
  );
}
