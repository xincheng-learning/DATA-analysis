"use client";
import {memo,useCallback,useEffect,useLayoutEffect,useRef,useState,type PointerEvent as ReactPointerEvent} from 'react';
import {ArrowLeft,ArrowUpRight,Check,GripHorizontal,LayoutDashboard,MessageSquare,Plus,RefreshCw,StickyNote,Trash2,X,ZoomIn,ZoomOut} from 'lucide-react';
import {ChartView} from './chart-view';
import {PanelPicker} from './panel-picker';
import {boardSummary,readBoard,removeBoard,saveBoard,snap,type AnalysisBoard,type BoardItem,type BoardSummary} from '@/lib/boards';
import type {ChartAppearance} from '@/lib/chart-appearance';

const MemoChart=memo(ChartView);
type Rect=Pick<BoardItem,'x'|'y'|'width'|'height'>;
type Arrival={x:number;y:number;width:number;height:number};

function Preview({item}:{item:BoardItem}) {
  const data=Array.isArray(item.spec?.data)?item.spec.data as {x?:unknown[];y?:unknown[];z?:unknown[][];type?:string}[]:[];
  const values=(data[0]?.y || []).filter((v):v is number=>typeof v==='number'&&Number.isFinite(v)).slice(0,20);
  const max=Math.max(...values.map(Math.abs),1);
  if(data[0]?.type==='heatmap'&&Array.isArray(data[0].z)){
    const cells=data[0].z,maxCell=Math.max(1,...cells.flat().filter((value):value is number=>typeof value==='number'));
    return <div className="panel-material-preview" aria-hidden="true"><svg viewBox="0 0 220 60">{cells.flatMap((row,y)=>row.map((value,x)=><rect key={`${y}:${x}`} x={x*220/row.length+2} y={y*60/cells.length+2} width={220/row.length-4} height={60/cells.length-4} rx="2" fill="currentColor" opacity={typeof value==='number'?.15+.85*value/maxCell:0}/>))}</svg></div>;
  }
  if(data[0]?.type==='scatter'&&values.length>1)return <div className="panel-material-preview" aria-hidden="true"><svg viewBox="0 0 220 60"><polyline points={values.map((value,index)=>`${4+index*212/(values.length-1)},${56-Math.abs(value)/max*48}`).join(' ')} fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinejoin="round"/></svg></div>;
  return <div className="panel-material-preview" aria-hidden="true">{values.length?<svg viewBox="0 0 220 60" preserveAspectRatio="none">{values.map((value,index)=><rect key={index} x={index*220/values.length+3} y={57-Math.abs(value)/max*47} width={Math.max(3,220/values.length-7)} height={Math.max(2,Math.abs(value)/max*47)} rx="2" fill="currentColor" opacity={.4+index/values.length*.6}/>)}</svg>:<LayoutDashboard size={28}/>}</div>;
}

export function CanvasFrame({item,zoom,arrival,returning=false,onChange,onRemove,onReturnDrop,onDragPosition,onAppearanceChange}:{item:BoardItem;zoom:number;arrival?:Arrival;returning?:boolean;onChange:(item:BoardItem)=>void;onRemove:()=>void;onReturnDrop:(x:number,y:number)=>boolean;onDragPosition:(x:number,y:number,active:boolean,box?:Rect)=>void;onAppearanceChange?:(appearance:ChartAppearance)=>Promise<void>}) {
  const [preview,setPreview]=useState<(Rect&{mode:'move'|'resize'})|null>(null),[expanded,setExpanded]=useState(!arrival),[chartReady,setChartReady]=useState(item.kind!=='chart'||!arrival);
  const frame=useRef<HTMLElement>(null),landing=useRef<Animation|null>(null);
  const ready=useCallback(()=>setChartReady(true),[]);
  useLayoutEffect(()=>{
    if(!arrival||!frame.current)return;
    const bounds=frame.current.getBoundingClientRect();
    const scale=bounds.width/frame.current.offsetWidth;
    const transform=frame.current.style.transform;
    // Animate from the actual floating card, without changing the chart's layout size.
    const animation=frame.current.animate([
      {transform:`${transform} translate3d(${(arrival.x-bounds.left)/scale}px,${(arrival.y-bounds.top)/scale}px,0) scale(${arrival.width/bounds.width},${arrival.height/bounds.height})`},
      {transform},
    ],{duration:window.matchMedia('(prefers-reduced-motion: reduce)').matches?0:420,easing:'cubic-bezier(.22,1,.36,1)'});
    landing.current=animation;animation.onfinish=()=>setExpanded(true);
    return()=>animation.cancel();
  },[arrival]);
  const gesture=useRef<{pointer:number;x:number;y:number;box:Rect;mode:'move'|'resize'}|null>(null);
  const moving=preview?.mode==='move';
  const box=preview||item;
  function start(event:ReactPointerEvent,mode:'move'|'resize') {
    if(event.button!==0 || (event.target as HTMLElement).closest('button,input,textarea'))return;
    landing.current?.cancel();setExpanded(true);
    event.preventDefault();event.stopPropagation();event.currentTarget.setPointerCapture(event.pointerId);
    gesture.current={pointer:event.pointerId,x:event.clientX,y:event.clientY,box:{x:item.x,y:item.y,width:item.width,height:item.height},mode};
  }
  function rectangle(event:ReactPointerEvent,active:NonNullable<typeof gesture.current>) {
    const dx=(event.clientX-active.x)/zoom,dy=(event.clientY-active.y)/zoom;
    return active.mode==='move'?{...active.box,x:Math.min(20000,snap(active.box.x+dx)),y:Math.min(20000,snap(active.box.y+dy))}:
      {...active.box,width:Math.min(4000,Math.max(item.kind==='chart'?280:160,snap(active.box.width+dx))),height:Math.min(4000,Math.max(item.kind==='chart'?300:80,snap(active.box.height+dy)))};
  }
  function move(event:ReactPointerEvent) {const active=gesture.current;if(!active)return;const next=rectangle(event,active);setPreview({...next,mode:active.mode});if(active.mode==='move')onDragPosition(event.clientX,event.clientY,true,next);}
  function end(event:ReactPointerEvent) {const active=gesture.current;if(!active)return;if(!(active.mode==='move'&&onReturnDrop(event.clientX,event.clientY)))onChange({...item,...rectangle(event,active)});gesture.current=null;setPreview(null);onDragPosition(0,0,false);}
  function cancel(){gesture.current=null;setPreview(null);onDragPosition(0,0,false);}
  const resizing=preview?.mode==='resize';
  return <section ref={frame} className={`board-frame ${item.kind==='text'?'board-note':''} ${preview?'is-moving':''} ${moving&&returning?'is-card-dragging':''}`} style={{width:box.width,height:box.height,transform:`translate3d(${box.x}px,${box.y}px,0)`}} data-item-id={item.id} aria-label={item.kind==='chart'?`面板图表：${item.title}`:'面板笔记'}>
    <div className="board-frame-handle" role="button" tabIndex={0} aria-label={`移动${item.title||'笔记'}`} onPointerDown={event=>start(event,'move')} onPointerMove={move} onPointerUp={end} onPointerCancel={cancel} onLostPointerCapture={cancel}
      onKeyDown={event=>{const delta:Record<string,[number,number]>={ArrowLeft:[-8,0],ArrowRight:[8,0],ArrowUp:[0,-8],ArrowDown:[0,8]};if(delta[event.key]){event.preventDefault();onChange({...item,x:snap(item.x+delta[event.key][0]),y:snap(item.y+delta[event.key][1])});}}}>
      <GripHorizontal size={13}/><strong>{item.appearance?.title?.trim()||item.title||(item.kind==='text'?'分析笔记':'分析图表')}</strong><button className="icon-button" aria-label={item.kind==='chart'?'移回素材列':'删除笔记'} title={item.kind==='chart'?'移回素材列':'删除笔记'} onClick={onRemove}><X size={14}/></button>
    </div>
    <div className="board-frame-content" style={resizing?{width:item.width,height:item.height-34,transform:`scale(${box.width/item.width},${(box.height-34)/(item.height-34)})`,transformOrigin:'top left',pointerEvents:'none'}:undefined}>
      {item.kind==='chart'&&item.spec?<><MemoChart spec={item.spec} onReady={ready} appearance={item.appearance} onAppearanceChange={onAppearanceChange}/>{item.scope&&<p className="board-chart-scope">{item.scope}</p>}</>:
        <textarea aria-label="笔记内容" placeholder="记录观察、假设或接下来要查的问题…" value={item.text} maxLength={5000} onChange={event=>onChange({...item,text:event.target.value})}/>}
    </div>
    {arrival&&<div aria-hidden="true" className={`panel-material-landing ${expanded&&chartReady?'is-revealed':''}`}><Preview item={item}/><strong>{item.title}</strong><span>{chartReady?'展开分析图表':'正在准备交互图表…'}</span></div>}
    <div className="board-resize-handle" role="separator" tabIndex={0} aria-label={`调整${item.title||'笔记'}大小`} onPointerDown={event=>start(event,'resize')} onPointerMove={move} onPointerUp={end} onPointerCancel={cancel} onLostPointerCapture={cancel}
      onKeyDown={event=>{if(['ArrowRight','ArrowLeft','ArrowUp','ArrowDown'].includes(event.key)){event.preventDefault();onChange({...item,width:Math.max(item.kind==='chart'?280:160,item.width+(event.key==='ArrowRight'?16:event.key==='ArrowLeft'?-16:0)),height:Math.max(item.kind==='chart'?300:80,item.height+(event.key==='ArrowDown'?16:event.key==='ArrowUp'?-16:0))});}}}/>
  </section>;
}

export function AnalysisPanels({initialId,onClose,onAnalyse,active=true,onSaveReady}:{initialId?:string;onClose:()=>void;onAnalyse:(board:BoardSummary)=>void;active?:boolean;onSaveReady?:(save:()=>Promise<void>)=>void}) {
  const [board,setBoard]=useState<AnalysisBoard|null>(null),[picker,setPicker]=useState(!initialId),[error,setError]=useState(''),[status,setStatus]=useState('已保存'),[zoom,setZoom]=useState(1),[deleting,setDeleting]=useState(false);
  const [drag,setDrag]=useState<{item:BoardItem;x:number;y:number;origin?:'material'|'canvas';box?:Rect;dropBox?:Rect}|null>(null),[returning,setReturning]=useState(false);
  const [arrival,setArrival]=useState<{id:string;from:Arrival}|null>(null);
  const [opening,setOpening]=useState(false);
  const current=useRef<AnalysisBoard|null>(null),dirty=useRef(false),job=useRef<Promise<void>|null>(null),timer=useRef<ReturnType<typeof setTimeout>|null>(null);
  const viewport=useRef<HTMLDivElement>(null),canvas=useRef<HTMLDivElement>(null),materialsRef=useRef<HTMLElement>(null),ghostRef=useRef<HTMLDivElement>(null),request=useRef(0);
  const flush=useCallback(async ():Promise<void>=>{
    if(timer.current)clearTimeout(timer.current);
    if(job.current)return job.current;
    if(!dirty.current||!current.current)return;
    job.current=(async()=>{setStatus('保存中…');setError('');
      try {while(dirty.current&&current.current){dirty.current=false;const result=await saveBoard(current.current);current.current={...current.current,version:result.version};setBoard(value=>value?{...value,version:result.version}:value);}setStatus('已保存');}
      catch(reason){dirty.current=true;setStatus('未保存');setError((reason as Error).message);throw reason;}
      finally{job.current=null;}
    })();return job.current;
  },[]);
  function change(next:AnalysisBoard) {const value={...next,version:current.current?.id===next.id?current.current.version:next.version};current.current=value;setBoard(value);dirty.current=true;setStatus('待保存');if(timer.current)clearTimeout(timer.current);timer.current=setTimeout(()=>{void flush().catch(()=>{});},450);}
  const open=useCallback(async (id:string)=>{
    await flush();const token=++request.current;setError('');setOpening(true);
    try{const value=await readBoard(id);
      if(token!==request.current)return;current.current=value;dirty.current=false;setBoard(value);setStatus('已保存');setZoom(1);setArrival(null);
    }finally{if(token===request.current)setOpening(false);}
  },[flush]);
  useEffect(()=>{if(active){const id=initialId||current.current?.id;if(id)void open(id).catch(reason=>setError(reason.message));}},[initialId,active,open]);
  useEffect(()=>()=>{if(timer.current)clearTimeout(timer.current);request.current++;},[]);
  useEffect(()=>{onSaveReady?.(flush);return()=>onSaveReady?.(async()=>{});},[onSaveReady,flush]);
  useEffect(()=>{const prevent=(event:BeforeUnloadEvent)=>{if(dirty.current||job.current){event.preventDefault();event.returnValue='';}};window.addEventListener('beforeunload',prevent);return()=>window.removeEventListener('beforeunload',prevent);},[]);
  async function close(){try{await flush();onClose();}catch{/* Preserve unsaved edits on screen. */}}
  function update(item:BoardItem){if(current.current)change({...current.current,items:current.current.items.map(old=>old.id===item.id?item:old)});}
  async function saveAppearance(id:string,appearance:ChartAppearance){
    const previous=current.current!.items.find(item=>item.id===id)!.appearance;
    const apply=(value:ChartAppearance|undefined)=>{const latest=current.current!;change({...latest,items:latest.items.map(item=>item.id===id?{...item,appearance:value}:item)});};
    apply(appearance);
    try{await flush();}catch(reason){
      // Roll back only this style; preserve other layout and note edits made during saving.
      apply(previous);throw reason;
    }
  }
  function place(item:BoardItem,x?:number,y?:number){update({...item,placed:true,x:snap(x??((viewport.current?.scrollLeft||0)/zoom+32)),y:snap(y??((viewport.current?.scrollTop||0)/zoom+32))});}
  function overMaterials(x:number,y:number){const bounds=materialsRef.current?.getBoundingClientRect();return !!bounds&&x>=bounds.left&&x<=bounds.right&&y>=bounds.top&&y<=bounds.bottom;}
  function addText(){if(!current.current)return;const item:BoardItem={id:crypto.randomUUID().replaceAll('-',''),kind:'text',title:'分析笔记',text:'',placed:true,x:snap((viewport.current?.scrollLeft||0)/zoom+40),y:snap((viewport.current?.scrollTop||0)/zoom+40),width:220,height:120};change({...current.current,items:[...current.current.items,item]});}
  const placed=board?.items.filter(item=>item.placed)||[],materials=board?.items.filter(item=>item.kind==='chart'&&!item.placed)||[];
  const width=Math.max(1600,...placed.map(item=>item.x+item.width+160)),height=Math.max(1000,...placed.map(item=>item.y+item.height+160));
  function dragMaterial(item:BoardItem,x:number,y:number){
    const canvasBounds=canvas.current?.getBoundingClientRect(),viewBounds=viewport.current?.getBoundingClientRect();
    const dropBox=canvasBounds&&viewBounds&&x>=viewBounds.left&&x<=viewBounds.right&&y>=viewBounds.top&&y<=viewBounds.bottom?
      {x:snap((x-canvasBounds.left)/zoom-24),y:snap((y-canvasBounds.top)/zoom-24),width:item.width,height:item.height}:undefined;
    setDrag({item,x,y,origin:'material',dropBox});
  }
  const dropBox=drag?.dropBox;
  async function analyse(){try{await flush();if(current.current)onAnalyse(boardSummary(current.current));}catch{/* Keep the draft open. */}}
  if(!active)return null;
  if(opening)return <main className="analysis-panels" aria-label="分析面板编辑器" aria-busy="true"><div className="panel-library-empty" role="status">正在读取分析面板…</div></main>;
  return <main className="analysis-panels" aria-label="分析面板编辑器">
    <header className="panels-header"><button className="panel-back" onClick={()=>void close()}><ArrowLeft size={16}/>返回聊天</button><div className="panels-heading"><span>ANALYSIS BOARD</span>{board?<input value={board.title} aria-label="面板名称" maxLength={100} onChange={event=>change({...board,title:event.target.value})}/>:<h1>你的分析面板</h1>}</div><span className="panel-save-state" role="status">{status==='已保存'&&<Check size={12}/>} {status}</span><button className="panel-action" onClick={()=>setPicker(true)}><LayoutDashboard size={14}/>切换 / 新建</button></header>
    {board?<><div className="panels-toolbar"><div><button onClick={addText}><StickyNote size={15}/>添加文本</button><button onClick={()=>void analyse()}><MessageSquare size={15}/>在聊天中分析<ArrowUpRight size={12}/></button></div><div><button aria-label="缩小画布" onClick={()=>setZoom(value=>Math.max(.35,value-.1))}><ZoomOut size={15}/></button><span>{Math.round(zoom*100)}%</span><button aria-label="放大画布" onClick={()=>setZoom(value=>Math.min(1.5,value+.1))}><ZoomIn size={15}/></button><button onClick={()=>setZoom(Math.max(.35,Math.min(1,(viewport.current?.clientWidth||1000)/width)))}>适应窗口</button><button aria-label="删除当前面板" onClick={()=>setDeleting(true)}><Trash2 size={15}/></button></div></div>
      {error&&<div className="panel-error" role="alert">{error}<button onClick={()=>void flush().catch(()=>{})}><RefreshCw size={13}/>重试保存</button><button onClick={()=>{if(confirm('重新载入会放弃当前未保存的布局与笔记，继续吗？')){dirty.current=false;void open(board.id).catch(reason=>setError(reason.message));}}}>重新载入</button></div>}
      <div className="panels-body"><aside ref={materialsRef} className={`panel-materials ${returning?'is-returning':''}`}><div className="panel-materials-heading"><strong>{returning?'松开回到素材库':'图表素材'}</strong><span>{materials.length}</span></div><p>{returning?'保留图表，下次可以再次拖入。':'从这里拖入画布，也可以将画布图表拖回这里。'}</p>{materials.length===0&&<div className="panel-material-empty"><LayoutDashboard size={24}/><strong>{returning?'松开即可收回':'等待新的图表'}</strong><span>在聊天图表上点击“加入面板”。已放置的图表可拖回这里。</span></div>}
        {materials.map(item=><div key={item.id} className="panel-material" tabIndex={0} aria-label={`素材：${item.title}`} onPointerDown={event=>{if(event.button!==0||(event.target as HTMLElement).closest('button'))return;event.preventDefault();event.currentTarget.setPointerCapture(event.pointerId);dragMaterial(item,event.clientX,event.clientY);}}
          onPointerMove={event=>{if(drag?.item.id===item.id)dragMaterial(item,event.clientX,event.clientY);}}
          onPointerUp={event=>{if(!drag)return;const bounds=viewport.current?.getBoundingClientRect(),origin=canvas.current?.getBoundingClientRect();if(bounds&&origin&&event.clientX>=bounds.left&&event.clientX<=bounds.right&&event.clientY>=bounds.top&&event.clientY<=bounds.bottom){const ghost=ghostRef.current?.getBoundingClientRect();setArrival({id:item.id,from:{x:event.clientX+14,y:event.clientY+14,width:ghost?.width||210,height:ghost?.height||145}});place(item,(event.clientX-origin.left)/zoom-24,(event.clientY-origin.top)/zoom-24);}setDrag(null);}}
          onPointerCancel={()=>setDrag(null)} onLostPointerCapture={()=>setDrag(null)} onKeyDown={event=>{if(event.key==='Enter'){setArrival(null);place(item);}}}><Preview item={item}/><strong>{item.title}</strong><div><button onClick={()=>{setArrival(null);place(item);}}><Plus size={12}/>放入画布</button><button aria-label={`移除素材${item.title}`} onClick={()=>change({...board,items:board.items.filter(old=>old.id!==item.id)})}><X size={12}/></button></div></div>)}
      </aside><div ref={viewport} className={`panel-canvas-viewport ${drag?'is-dropping':''}`} aria-label="自由分析画布"><div className="panel-canvas-size" style={{width:width*zoom,height:height*zoom}}><div ref={canvas} className="panel-canvas" style={{width,height,transform:`scale(${zoom})`,transformOrigin:'top left'}}>
        {!placed.length&&<div className="panel-canvas-empty"><LayoutDashboard size={35} strokeWidth={1}/><h2>把分析摆在一起</h2><p>从左侧拖入图表，或添加一段笔记。<br/>位置和大小，由你决定。</p><button className="panel-action" onClick={addText}><Plus size={14}/>添加第一条笔记</button></div>}
        {dropBox&&<div className="panel-drop-target" style={{width:dropBox.width,height:dropBox.height,transform:`translate3d(${dropBox.x}px,${dropBox.y}px,0)`}}><span>松开放置</span></div>}
        {placed.map(item=><CanvasFrame key={item.id} item={item} zoom={zoom} arrival={arrival?.id===item.id?arrival.from:undefined} returning={returning&&drag?.item.id===item.id} onChange={update} onAppearanceChange={appearance=>saveAppearance(item.id,appearance)} onDragPosition={(x,y,active,box)=>{setReturning(item.kind==='chart'&&active&&overMaterials(x,y));setDrag(active?{item,x,y,origin:'canvas',box}:null);}} onReturnDrop={(x,y)=>{if(item.kind==='chart'&&overMaterials(x,y)){update({...item,placed:false});return true;}return false;}} onRemove={()=>item.kind==='chart'?update({...item,placed:false}):change({...board,items:board.items.filter(old=>old.id!==item.id)})}/>)}
      </div></div></div></div>
    </>:<div className="panel-library-empty"><LayoutDashboard size={42} strokeWidth={1}/><h2>让每一次发现相互连接</h2><p>把不同聊天里的图表汇集在一起，加入笔记，再交给分析助手综合解读。</p><button className="panel-action" onClick={()=>setPicker(true)}><Plus size={15}/>创建或打开面板</button>{error&&<p role="alert">{error}</p>}</div>}
    {drag&&(drag.origin==='material'||returning)&&<div ref={ghostRef} className="panel-drag-ghost" style={{transform:`translate3d(${drag.x+14}px,${drag.y+14}px,0)`}}>{drag.item.kind==='text'?<div className="panel-note-preview"><StickyNote size={17}/><p>{drag.item.text.slice(0,90)||'记录你的观察…'}</p></div>:<Preview item={drag.item}/>}<strong>{drag.item.title}</strong><span>{returning?'松开回到素材库':'松开放入画布'}</span></div>}
    {picker&&<PanelPicker onPick={value=>open(value.id)} onClose={()=>setPicker(false)}/>}
    {deleting&&board&&<div className="panel-delete-overlay" role="alertdialog" aria-label="删除分析面板"><div><h2>删除“{board.title}”？</h2><p>将删除此面板的素材和笔记。原聊天与数据仍然保留。</p><button onClick={()=>setDeleting(false)}>取消</button><button className="panel-danger" onClick={async()=>{try{await flush();await removeBoard(board.id);current.current=null;setBoard(null);setDeleting(false);setPicker(true);}catch(reason){setError((reason as Error).message);setDeleting(false);}}}>删除面板</button></div></div>}
  </main>;
}
