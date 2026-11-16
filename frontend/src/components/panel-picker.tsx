"use client";
import {useEffect,useState} from 'react';
import {Dialog} from '@base-ui/react/dialog';
import {LayoutDashboard,Plus,X} from 'lucide-react';
import {createBoard,listBoards,boardSummary,type BoardSummary} from '@/lib/boards';

export function PanelPicker({title='选择分析面板',onPick,onClose}:{title?:string;onPick:(board:BoardSummary)=>Promise<void>|void;onClose:()=>void}) {
  const [boards,setBoards]=useState<BoardSummary[]>([]),[name,setName]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true);
  useEffect(()=>{let active=true;listBoards().then(data=>{if(active)setBoards(data);}).catch(err=>{if(active)setError(err.message);}).finally(()=>{if(active)setLoading(false);});return()=>{active=false;};},[]);
  async function pick(board:BoardSummary) {setBusy(true);setError('');try{await onPick(board);onClose();}catch(err){setError((err as Error).message);}finally{setBusy(false);}}
  async function create() {if(!name.trim())return;setBusy(true);setError('');try{const board=boardSummary(await createBoard(name.trim()));setBoards(old=>[board,...old]);setName('');await onPick(board);onClose();}catch(err){setError((err as Error).message);}finally{setBusy(false);}}
  return <Dialog.Root open onOpenChange={open=>{if(!open&&!busy)onClose();}}><Dialog.Portal><Dialog.Backdrop className="panel-modal-backdrop"/><Dialog.Popup className="panel-picker">
    <div className="panel-modal-heading"><LayoutDashboard size={19}/><Dialog.Title>{title}</Dialog.Title><button className="icon-button" aria-label="关闭面板选择" disabled={busy} onClick={onClose}><X size={17}/></button></div>
    <Dialog.Description className="panel-description">选择要使用的面板，图表与笔记保存在本机。</Dialog.Description>
    <div className="panel-picker-list">{loading?<p role="status">正在读取…</p>:boards.length?boards.map(board=><button key={board.id} disabled={busy} onClick={()=>void pick(board)}><LayoutDashboard size={18}/><span><strong>{board.title}</strong><small>{board.chart_count} 张图表 · {board.note_count} 条笔记</small></span><span>选择 →</span></button>):<p>还没有面板，可在下方创建。</p>}</div>
    <form className="panel-create-row" onSubmit={event=>{event.preventDefault();void create();}}><input aria-label="新面板名称" maxLength={100} value={name} onChange={event=>setName(event.target.value)} placeholder="新面板名称" disabled={busy}/><button className="panel-action" disabled={busy||!name.trim()}><Plus size={14}/>新建并选择</button></form>
    {error&&<p role="alert" className="panel-error">{error}</p>}
  </Dialog.Popup></Dialog.Portal></Dialog.Root>;
}
