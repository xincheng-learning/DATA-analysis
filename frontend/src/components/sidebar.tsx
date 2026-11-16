"use client";

import {useEffect, useRef, useState} from 'react';
import {Dialog} from '@base-ui/react/dialog';
import {Plus, Trash2, MessageSquare, MoreHorizontal, Pencil, X, Settings, Folder, FolderPlus, Search, ChevronRight, LayoutDashboard, ChartNoAxesCombined, LogOut} from 'lucide-react';
import {ThemeToggle} from '@/components/theme-toggle';
import {localRequest, type ChatFolder, type SessionListItem} from '@/lib/api';

interface SidebarProps {
  sessions: SessionListItem[]; activeSessionId: string | null;
  onNewChat: () => void; onSelectSession: (sid: string) => void;
  onDeleteSession: (sid: string) => Promise<void>;
  onRenameSession?: (sid: string, title: string) => Promise<void>;
  onRefresh?: () => Promise<void>; onSignOut?: () => void; userEmail?: string;
  onModelSettings?: () => void; onStorageSettings?: () => void; settingsDisabled?: boolean;
  onPanels?: () => void; panelsActive?: boolean;
}

export function Sidebar({sessions,activeSessionId,onNewChat,onSelectSession,onDeleteSession,onRenameSession,onRefresh,
  onSignOut,userEmail,onModelSettings,onStorageSettings,settingsDisabled,onPanels,panelsActive}: SidebarProps) {
  const [folders,setFolders]=useState<ChatFolder[]>([]),[filter,setFilter]=useState('all'),[search,setSearch]=useState('');
  const [managing,setManaging]=useState(false),[selected,setSelected]=useState<string[]>([]);
  const [menu,setMenu]=useState<string|null>(null),[settings,setSettings]=useState(false);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [editor,setEditor]=useState<{kind:'create'|'folder'|'chat';id:string;title:string}|null>(null);
  const [deleting,setDeleting]=useState<string[]|null>(null),[removeFolder,setRemoveFolder]=useState<ChatFolder|null>(null);
  const menuRef=useRef<HTMLDivElement>(null);
  const local=Boolean(onStorageSettings), disabled=Boolean(settingsDisabled||busy);
  const visible=sessions.filter(s=>(filter==='all'||(filter==='none'?!s.folder_id:s.folder_id===filter))&&
    (s.title||'未命名对话').toLocaleLowerCase().includes(search.toLocaleLowerCase()));
  const chosen=selected.filter(id=>sessions.some(s=>s.session_id===id));
  const folder=folders.find(f=>f.id===filter);
  useEffect(()=>{
    if(!local)return;
    let active=true;
    localRequest('/chat-folders').then(items=>{if(active)setFolders(items);}).catch(err=>{if(active)setError(err.message);});
    return()=>{active=false;};
  },[local]);
  useEffect(()=>{
    function outside(event:MouseEvent){if(menuRef.current&&!menuRef.current.contains(event.target as Node))setMenu(null);}
    function escape(event:KeyboardEvent){if(event.key==='Escape'){setMenu(null);setSettings(false);}}
    document.addEventListener('mousedown',outside);document.addEventListener('keydown',escape);
    return()=>{document.removeEventListener('mousedown',outside);document.removeEventListener('keydown',escape);};
  },[]);
  async function action(run:()=>Promise<void>){
    setBusy(true);setError('');setNotice('');
    try{await run();}catch(err){setError(err instanceof Error?err.message:'操作未完成，请重试。');}
    finally{setBusy(false);}
  }
  async function refreshFolders(){setFolders(await localRequest('/chat-folders'));}
  async function saveName(){
    if(!editor||!editor.title.trim())return;
    await action(async()=>{
      if(editor.kind==='chat')await onRenameSession?.(editor.id,editor.title.trim());
      else{
        const saved=await localRequest(`/chat-folders${editor.kind==='folder'?`/${editor.id}`:''}`,editor.kind==='folder'?'PATCH':'POST',{title:editor.title.trim()});
        setFolders(items=>editor.kind==='create'?[...items,saved]:items.map(item=>item.id===editor.id?{...item,title:editor.title.trim()}:item));
        setEditor(null);
        await refreshFolders();
      }
      setEditor(null);
    });
  }
  async function move(ids:string[],target:string){
    await action(async()=>{
      await localRequest('/chat-folders/move','POST',{session_ids:ids,folder_id:target==='none'?null:target});
      setSelected([]);setMenu(null);setNotice(`已移动 ${ids.length} 场聊天。`);await onRefresh?.();
    });
  }
  async function confirmDelete(){
    if(!deleting)return;
    await action(async()=>{
      const failures:string[]=[],failed:string[]=[];
      for(const id of deleting){
        try{await onDeleteSession(id);}catch(err){failed.push(id);failures.push(`${sessions.find(s=>s.session_id===id)?.title||'未命名对话'}：${err instanceof Error?err.message:'删除失败'}`);}
      }
      setSelected(failed);setDeleting(null);setNotice(`已删除 ${deleting.length-failed.length} 场聊天${failed.length?`，${failed.length} 场未删除。`:'。'}`);
      if(failures.length)setError(failures.join('\n'));
    });
  }
  function toggle(id:string){setSelected(ids=>ids.includes(id)?ids.filter(x=>x!==id):[...ids,id]);}
  const dialogOpen=Boolean(editor||deleting||removeFolder);
  return <nav className="sidebar" aria-label="会话列表">
    <div className="sidebar-brand"><div className="brand-lockup"><span><ChartNoAxesCombined size={22} strokeWidth={1.6}/></span><div><strong>DATA analysis</strong><small>和数据一起思考</small></div></div><ThemeToggle/></div>
    <div className="sidebar-shortcuts"><button className="sidebar-new" onClick={onNewChat} disabled={disabled}><Plus size={17}/>新建对话</button>
      {onPanels&&<button className={panelsActive?'is-active':''} onClick={onPanels} disabled={disabled}><LayoutDashboard size={16}/>分析面板<ChevronRight size={13}/></button>}
    </div>
    <label className="sidebar-search"><Search size={14}/><input aria-label="搜索聊天" placeholder="搜索聊天" value={search} onChange={event=>setSearch(event.target.value)}/></label>
    {local&&<div className="sidebar-folders">
      <div className="sidebar-section-label"><span>文件夹</span><button aria-label="新建聊天文件夹" title="新建文件夹" disabled={disabled} onClick={()=>setEditor({kind:'create',id:'',title:''})}><FolderPlus size={15}/></button></div>
      <div className="folder-filters"><button aria-pressed={filter==='all'} onClick={()=>setFilter('all')}>全部聊天 <span>{sessions.length}</span></button><button aria-pressed={filter==='none'} onClick={()=>setFilter('none')}>未归类 <span>{sessions.filter(s=>!s.folder_id).length}</span></button></div>
      <div className="folder-list">{folders.map(f=><div className="folder-row" key={f.id}><button className={filter===f.id?'is-active':''} aria-pressed={filter===f.id} onClick={()=>{setFilter(f.id);setSelected([]);}}><Folder size={15}/><span>{f.title}</span><small>{sessions.filter(s=>s.folder_id===f.id).length}</small></button>
        <button className="folder-edit" aria-label={`管理文件夹 ${f.title}`} disabled={disabled} onClick={()=>setEditor({kind:'folder',id:f.id,title:f.title})}><MoreHorizontal size={14}/></button></div>)}</div>
    </div>}
    <div className="sidebar-section-label"><span>{folder?.title|| (filter==='none'?'未归类':'最近聊天')} · {visible.length}</span><button disabled={disabled} aria-pressed={managing} onClick={()=>{setManaging(!managing);setSelected([]);setMenu(null);}}>{managing?'完成':'管理'}</button></div>
    {managing&&<div className="chat-bulk-actions"><label><input type="checkbox" aria-label="选择当前列表全部聊天" checked={visible.length>0&&visible.every(s=>chosen.includes(s.session_id))} disabled={disabled||!visible.length} onChange={event=>setSelected(event.target.checked?Array.from(new Set([...chosen,...visible.map(s=>s.session_id)])):chosen.filter(id=>!visible.some(s=>s.session_id===id)))}/>当前列表</label><span>已选 {chosen.length}</span>
      {local&&<select aria-label="批量移动到文件夹" value="" disabled={disabled||!chosen.length||chosen.length>500} onChange={event=>void move(chosen,event.target.value)}><option value="" disabled>移动到…</option><option value="none">未归类</option>{folders.map(f=><option key={f.id} value={f.id}>{f.title}</option>)}</select>}
      <button className="panel-danger" disabled={disabled||!chosen.length} onClick={()=>setDeleting(chosen)}><Trash2 size={13}/>删除</button>
    </div>}
    <div className="sidebar-chats">{visible.map(s=><div className={`chat-row ${s.session_id===activeSessionId?'is-active':''}`} key={s.session_id}>
      {managing?<label className="chat-select"><input type="checkbox" aria-label={`选择聊天 ${s.title||'未命名对话'}`} checked={chosen.includes(s.session_id)} disabled={disabled} onChange={()=>toggle(s.session_id)}/><span>{s.title||'未命名对话'}</span></label>:<>
        <button className="chat-open" title={s.title||'未命名对话'} disabled={disabled} onClick={()=>onSelectSession(s.session_id)}><MessageSquare size={14}/><span>{s.title||'未命名对话'}</span></button>
        <div className="chat-options" ref={menu===s.session_id?menuRef:undefined}><button className="chat-menu-button" aria-label={`对话选项 ${s.title||'未命名对话'}`} aria-expanded={menu===s.session_id} disabled={disabled} onClick={()=>setMenu(menu===s.session_id?null:s.session_id)}><MoreHorizontal size={15}/></button>
          {menu===s.session_id&&<div className="chat-menu"><button onClick={()=>{setEditor({kind:'chat',id:s.session_id,title:s.title||''});setMenu(null);}}><Pencil size={13}/>重命名</button>
            {local&&<label>移动到文件夹<select aria-label="移动当前聊天" value={s.folder_id||'none'} onChange={event=>void move([s.session_id],event.target.value)}><option value="none">未归类</option>{folders.map(f=><option key={f.id} value={f.id}>{f.title}</option>)}</select></label>}
            <button className="panel-danger" onClick={()=>{setDeleting([s.session_id]);setMenu(null);}}><Trash2 size={13}/>删除聊天</button></div>}
        </div></>}
    </div>)}{!visible.length&&<p className="sidebar-empty">{search?'没有匹配的聊天。':folder?'这个文件夹还没有聊天，可在“管理”中移动。':'还没有聊天，开始一次新的分析。'}</p>}</div>
    {notice&&<p className="sidebar-feedback" role="status">{notice}</p>}
    {error&&<div className="sidebar-feedback text-destructive" role="alert"><p>{error}</p><button disabled={disabled} onClick={()=>void action(async()=>{await refreshFolders();await onRefresh?.();})}>刷新列表</button><button onClick={()=>setError('')}>关闭</button></div>}
    <div className="sidebar-footer"><button className="sidebar-settings" aria-expanded={settings} onClick={()=>setSettings(!settings)}><Settings size={16}/>设置<span>{userEmail||'工作区'}</span></button>
      {settings&&<div className="sidebar-settings-menu">{onModelSettings&&<button disabled={disabled} onClick={()=>{setSettings(false);onModelSettings();}}>模型设置</button>}{onStorageSettings&&<button disabled={disabled} onClick={()=>{setSettings(false);onStorageSettings();}}>存储设置</button>}{onSignOut&&<button onClick={onSignOut}><LogOut size={14}/>退出登录</button>}</div>}
    </div>
    <Dialog.Root open={dialogOpen} onOpenChange={open=>{if(!open&&!busy){setEditor(null);setDeleting(null);setRemoveFolder(null);}}}><Dialog.Portal><Dialog.Backdrop className="fixed inset-0 z-50 bg-black/40"/><Dialog.Popup className="chat-manage-dialog">
      <header><Dialog.Title>{deleting?`删除 ${deleting.length} 场聊天？`:removeFolder?'移除文件夹？':editor?.kind==='create'?'新建文件夹':editor?.kind==='folder'?'管理文件夹':'重命名聊天'}</Dialog.Title><button aria-label="关闭聊天管理" disabled={busy} onClick={()=>{setEditor(null);setDeleting(null);setRemoveFolder(null);}}><X size={18}/></button></header>
      <Dialog.Description>{deleting?'将删除下列聊天的消息、上传文件和清洗数据，无法撤销。独立保存的分析面板和高级视图仍保留。':removeFolder?'聊天和数据全部保留，移回“未归类”。':'文件夹仅用于归类聊天，各场聊天的数据仍独立保存。'}</Dialog.Description>
      {deleting&&<ul>{deleting.map(id=><li key={id}>{sessions.find(s=>s.session_id===id)?.title||id}</li>)}</ul>}
      {editor&&<form onSubmit={event=>{event.preventDefault();void saveName();}}><label>名称<input aria-label={editor.kind==='chat'?'对话名称':'文件夹名称'} autoFocus required maxLength={editor.kind==='chat'?100:60} value={editor.title} disabled={busy} onChange={event=>setEditor({...editor,title:event.target.value})}/></label><footer>{editor.kind==='folder'&&<button type="button" className="panel-danger" disabled={busy} onClick={()=>{setRemoveFolder({id:editor.id,title:editor.title});setEditor(null);}}>移除文件夹</button>}<button type="submit" disabled={busy||!editor.title.trim()}>{busy?'保存中…':'保存'}</button></footer></form>}
      {deleting&&<footer><button disabled={busy} onClick={()=>setDeleting(null)}>取消</button><button className="panel-danger" disabled={busy} onClick={()=>void confirmDelete()}>{busy?'正在逐项删除…':`确认删除 ${deleting.length} 场`}</button></footer>}
      {removeFolder&&<footer><button disabled={busy} onClick={()=>setRemoveFolder(null)}>取消</button><button disabled={busy} onClick={()=>void action(async()=>{await localRequest(`/chat-folders/${removeFolder.id}`,'DELETE');if(filter===removeFolder.id)setFilter('none');setFolders(items=>items.filter(item=>item.id!==removeFolder.id));setRemoveFolder(null);await onRefresh?.();})}>移除并保留聊天</button></footer>}
      {error&&<p role="alert" className="text-destructive">{error}</p>}
    </Dialog.Popup></Dialog.Portal></Dialog.Root>
  </nav>;
}
