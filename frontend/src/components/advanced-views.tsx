"use client";

import {useEffect,useRef,useState} from 'react';
import {Dialog} from '@base-ui/react/dialog';
import {X} from 'lucide-react';
import {localRequest,resultExportUrl,type AdvancedView,type Message,type AskResponse} from '@/lib/api';
import {ResultTable} from '@/components/data-panel';
import {AdvancedPresentation} from '@/components/advanced-presentation';
import {businessLabel,resultSources} from '@/lib/analysis';

const names:Record<string,string>={pivot_table:'透视表',treemap:'矩形树图',sunburst:'旭日图',sankey:'桑基图'};

export function advancedSources(messages:Pick<Message,'id'|'role'|'metadata'>[]){
  return messages.flatMap(message=>{
    if(message.role!=='assistant'||typeof message.id!=='number')return [];
    const meta=message.metadata as Partial<AskResponse>;
    const sources=resultSources(meta).map(item=>({data:item.metadata as Partial<AskResponse>,source_path:item.sourcePath,
      step_id:item.sourcePath.length===4?meta.workflow?.steps[Number(item.sourcePath[2])]?.id||'':''}));
    return sources.filter(({data})=>(data?.sql||data?.python_code&&data.checks?.some(check=>check.id==='python_result'&&check.status==='passed'))&&data.columns?.length&&data.data_version&&!data.error&&!data.result_error&&!data.validation_failure&&!data.output_limit_reached&&data.row_count&&data.row_count<=5000&&(data.result_id||data.row_count===data.rows?.length))
      .map(({data,step_id,source_path})=>({key:`${message.id}:${step_id||source_path.join('.')}`,message_id:message.id as number,step_id,source_path,data:data!}));
  });
}

function EmbeddedView({view}:{view:AdvancedView}){
  const mount=useRef<HTMLDivElement>(null),[error,setError]=useState('');
  useEffect(()=>{
    let active=true,unmount:(()=>void)|undefined;
    async function connect(){
      try{
        const settings=await localRequest(`/superset/views/${view.id}/token`,'POST',{});
        const {embedDashboard}=await import('@superset-ui/embedded-sdk');
        if(!active||!mount.current)return;
        const embedded=await embedDashboard({id:settings.embedded_id,supersetDomain:settings.origin,mountPoint:mount.current,
          fetchGuestToken:async()=>{const value=await localRequest(`/superset/views/${view.id}/token`,'POST',{});return value.token;},
          dashboardUiConfig:{hideTitle:true,hideChartControls:false,filters:{visible:true,expanded:false}},
          iframeTitle:view.title,referrerPolicy:'strict-origin-when-cross-origin'});
        if(active)unmount=embedded.unmount;else embedded.unmount();
      }catch(err){if(active)setError(err instanceof Error?err.message:'高级视图加载失败。');}
    }
    void connect();return()=>{active=false;unmount?.();};
  },[view.id,view.title]);
  return <>{error&&<p role="alert">{error}</p>}<div className="superset-embed" ref={mount}/></>;
}

export function AdvancedViews({onClose,sessionId,messages,initialId,onAnalyse}:{onClose:()=>void;sessionId:string|null;
  messages:Pick<Message,'id'|'role'|'metadata'>[];initialId?:string;onAnalyse:(view:AdvancedView)=>Promise<void>}){
  const [service,setService]=useState<{status:string;message:string}>(),[views,setViews]=useState<AdvancedView[]>([]);
  const [selected,setSelected]=useState(initialId||''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [source,setSource]=useState(''),[title,setTitle]=useState('高级分析'),[kind,setKind]=useState('pivot_table');
  const [dimensions,setDimensions]=useState<string[]>([]),[measure,setMeasure]=useState('');
  const [readback,setReadback]=useState<Partial<AskResponse>>(),[login,setLogin]=useState<{url:string;username:string;password:string}>();
  const [native,setNative]=useState<boolean|null>(null),[revision,setRevision]=useState(0);
  const sources=advancedSources(messages),current=sources.find(item=>item.key===source);
  const view=views.find(item=>item.id===selected);
  const displayNative=native??view?.chart_type==='pivot_table';
  useEffect(()=>{
    let active=true,polling=false;
    async function poll(){
      if(polling)return;polling=true;
      try{
        const [state,items]=await Promise.all([localRequest('/superset/status'),localRequest('/superset/views')]);
        if(active){setService(state);setViews(items);}
      }catch(err){if(active)setError(err instanceof Error?err.message:'高级服务状态读取失败。');}
      finally{polling=false;}
    }
    void poll();const timer=setInterval(poll,5000);return()=>{active=false;clearInterval(timer);};
  },[]);
  async function action(callback:()=>Promise<void>){
    setBusy(true);setError('');
    try{await callback();}catch(err){setError(err instanceof Error?err.message:'操作失败。');}finally{setBusy(false);}
  }
  function chooseSource(key:string){
    const item=sources.find(item=>item.key===key);setSource(key);
    setDimensions([]);setMeasure(item?.data.columns?.find(column=>typeof item.data.rows?.[0]?.[column]==='number')||'');
  }
  async function create(){
    if(!current||!sessionId)return;
    const saved=await localRequest('/superset/views','POST',{session_id:sessionId,message_id:current.message_id,
      source_path:current.source_path,title,chart_type:kind,dimensions,measure});
    setViews(items=>[saved,...items.filter(item=>item.id!==saved.id)]);setSelected(saved.id);setReadback(undefined);setNative(saved.chart_type==='pivot_table');
  }
  const working=busy||service?.status==='starting'||service?.status==='stopping';
  return <Dialog.Root open onOpenChange={open=>{if(!open&&!busy)onClose();}}><Dialog.Portal><Dialog.Backdrop className="fixed inset-0 z-50 bg-black/40"/>
    <Dialog.Popup className="advanced-popup">
      <header><div><Dialog.Title>高级可视化</Dialog.Title><Dialog.Description>从聊天结果继续探索，图形和数据一起核对。</Dialog.Description></div><button aria-label="关闭高级可视化" onClick={onClose} disabled={busy}><X size={20}/></button></header>
      <details className="advanced-service-settings" open={service?.status!=='ready'}><summary><span className={service?.status==='ready'?'status-dot configured':'status-dot'}/>{service?.status==='ready'?'Superset 已连接':'Superset 服务'}<span>服务与专业设置</span></summary><div className="advanced-service"><span role="status">{service?.status==='ready'?'高级服务已就绪':service?.message||'正在读取本机状态…'}</span>
        <button disabled={working||service?.status==='ready'} onClick={()=>void action(async()=>{await localRequest('/superset/service/start','POST',{});setService({status:'starting',message:'正在启动，保留已保存视图…'});})}>启动高级服务</button>
        <button disabled={working||service?.status!=='ready'} onClick={()=>void action(async()=>{await localRequest('/superset/service/stop','POST',{});setService({status:'stopping',message:'正在停止…'});})}>停止</button>
        <button disabled={busy||service?.status!=='ready'} onClick={()=>void action(async()=>setLogin(await localRequest('/superset/login','POST',{})))}>本机登录信息</button>
      </div>
      {login&&<div className="advanced-login"><a href={login.url} target="_blank" rel="noreferrer">打开 Superset ↗</a><span>账号：{login.username}</span><label>密码 <input type="password" readOnly value={login.password} autoComplete="off"/></label><button onClick={()=>void action(async()=>navigator.clipboard.writeText(login.password))}>复制密码</button><button onClick={()=>setLogin(undefined)}>隐藏</button></div>}
      </details>
      {error&&<p role="alert" className="text-destructive">{error}</p>}
      <div className="advanced-layout"><aside>
        <details className="advanced-create" open={!views.length}><summary>手动制作图表 <span>＋</span></summary><p>也可以在聊天中直接提出绘图要求。</p><form onSubmit={event=>{event.preventDefault();void action(create);}}><fieldset disabled={working||service?.status!=='ready'}>
          <label>查询结果<select aria-label="高级图表来源" value={source} onChange={event=>chooseSource(event.target.value)}><option value="">选择已保存的完整查询</option>{sources.map(item=><option key={item.key} value={item.key}>回复 {item.message_id}{item.step_id?` · ${item.step_id}`:''} · {item.data.row_count} 行</option>)}</select></label>
          {!sources.length&&<p>先在聊天中查询需要分析的数据，再制作高级图表。</p>}
          <label>名称<input value={title} onChange={event=>setTitle(event.target.value)} required maxLength={100}/></label>
          <label>图型<select aria-label="高级图型" value={kind} onChange={event=>setKind(event.target.value)}>{Object.entries(names).map(([id,name])=><option value={id} key={id}>{name}</option>)}</select></label>
          <label>指标<select aria-label="高级图表指标" value={measure} onChange={event=>{setMeasure(event.target.value);setDimensions(items=>items.filter(item=>item!==event.target.value));}} required><option value="">选择数值列</option>{current?.data.columns?.map(column=><option key={column} value={column}>{businessLabel(column)}</option>)}</select></label>
          <div className="advanced-dimensions"><span>维度（按勾选顺序）</span>{current?.data.columns?.filter(column=>column!==measure).map(column=><label key={column}><input type="checkbox" checked={dimensions.includes(column)} disabled={!dimensions.includes(column)&&dimensions.length>=4} onChange={event=>setDimensions(items=>event.target.checked?[...items,column]:items.filter(item=>item!==column))}/>{businessLabel(column)}</label>)}</div>
          <small>桑基图依次选择来源、去向；最多 5000 行，先按所选维度聚合。未知值、负权重和重复粒度会提示处理。</small>
          <button type="submit" disabled={!current||!dimensions.length||!measure||!title.trim()}>创建并核对</button>
        </fieldset></form></details>
        <h3>已保存视图</h3><div className="advanced-list">{views.map(item=><button key={item.id} className={selected===item.id?'selected':''} onClick={()=>{setSelected(item.id);setReadback(undefined);setNative(item.chart_type==='pivot_table');}}><strong>{item.title}</strong><span>{names[item.chart_type]} · {item.row_count} 行 · {item.status==='ready'?'已核对':item.status==='creating'?'正在创建':'未完成'}</span></button>)}{!views.length&&<p>在聊天里说出你想看的图表，Agent 会使用已保存的真实结果制作。</p>}</div>
      </aside><section className="advanced-content">
        {view?<><header><div><h3>{view.title}</h3><small>{names[view.chart_type]} · {view.row_count} 个维度组合</small></div><div><button disabled={working||service?.status!=='ready'||view.status!=='ready'} onClick={()=>void action(async()=>{setReadback(await localRequest(`/superset/views/${view.id}/read${sessionId?`?session_id=${encodeURIComponent(sessionId)}`:''}`,'POST',{}));setRevision(n=>n+1);})}>核对默认数据</button><button disabled={working||service?.status!=='ready'||view.status!=='ready'} onClick={()=>void action(()=>onAnalyse(view))}>回到聊天分析</button></div></header>
          <div className="advanced-display-tabs" role="group" aria-label="高级视图展示方式"><button aria-pressed={!displayNative} onClick={()=>setNative(false)}>图表展示</button><button aria-pressed={displayNative} onClick={()=>setNative(true)}>Superset 原生</button><span>{displayNative?'使用 Superset 原生筛选与交互':'统一绘图 · 数据由 Superset 回读核对'}</span></div>
          {view.checks.filter(check=>check.status==='warning').map(check=><p className="advanced-scope" key={check.id}>{check.detail}</p>)}
          {view.status!=='ready'?<p>{view.error||'高级视图尚未完成。'}</p>:service?.status==='ready'?displayNative?<EmbeddedView key={view.id} view={view}/>:<AdvancedPresentation key={view.id} view={view} revision={revision}/>:<p>启动高级服务后可以查看；已保存视图会保留。</p>}
          {readback&&<details open><summary>{readback.answer}</summary><ResultTable key={readback.result_id||view.id} columns={readback.columns||[]} rows={readback.rows||[]} totalRows={readback.row_count} sessionId={sessionId||undefined} resultId={readback.result_id||undefined}/>{sessionId&&readback.result_id&&<a className="view-chart-button" href={resultExportUrl(sessionId,readback.result_id)} download>下载完整结果 · {readback.row_count} 行 CSV</a>}</details>}
        </>:<div className="advanced-empty"><h3>让已核对的结果继续发挥作用</h3><p>可以在聊天里要求 Agent 制作透视表、矩形树图、旭日图或桑基图，也可以在左侧选取查询结果。</p></div>}
      </section></div><details className="advanced-boundary"><summary>数据范围与保存方式</summary><p>高级视图是独立快照，删除聊天或后续清洗不会删除它。筛选与钻取只覆盖保存维度；带回聊天时重新核对默认查询，页面临时筛选不自动带回。</p></details>
    </Dialog.Popup></Dialog.Portal></Dialog.Root>;
}
