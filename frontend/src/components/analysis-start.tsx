"use client";
import {ArrowUpRight,ChartNoAxesCombined,ScanLine,SearchCheck} from 'lucide-react';

const starts=[
  {label:'先检查数据',detail:'逐表体检，预览清洗影响',icon:ScanLine,
    question:'请先逐表检查数据粒度、缺失、重复和时间顺序，说明问题与分析影响。需要清洗时给出可查看的预览，先不要执行。'},
  {label:'开展业务分析',detail:'结论、理由与计算证据一起看',icon:SearchCheck,
    question:'请根据当前数据开展业务分析：先明确指标和范围，再分点给出结论、理由与计算证据。关键口径不确定时向我提问。'},
  {label:'制作交付图表',detail:'折线、柱状、散点等，可修改与导出',icon:ChartNoAxesCombined,
    question:'请根据当前数据和已确认的分析目标制作可交付图表，说明数据范围、单位和主要发现，保留可编辑与导出入口。'},
];

export function AnalysisStart({hasData=false,disabled=false,onChoose,onInspect,onRelate}:{hasData?:boolean;disabled?:boolean;onChoose:(question:string)=>void;onInspect?:()=>void;onRelate?:()=>void}){
  return <section className={`conversation-welcome analyst-start ${hasData?'has-data':''}`} aria-label="开始数据分析">
    <p className="analyst-start-label">DATA analysis</p>
    <h2>{hasData?'从这批数据开始。':'把数据变成有依据的判断。'}</h2>
    <p>{hasData?'选一个分析起点，按你的业务需要修改问题。':'上传表格或连接数据库，从体检、分析到可交付图表。'}</p>
    <div className="analysis-start-options">{starts.map(({label,detail,icon:Icon,question})=><button key={label} type="button" disabled={disabled} onClick={()=>onChoose(question)}>
      <Icon size={19} strokeWidth={1.5}/><span><strong>{label}</strong><small>{detail}</small></span><ArrowUpRight size={14}/>
    </button>)}</div>
    {hasData?<div className="analysis-start-coach">
      {onInspect&&<button type="button" disabled={disabled} onClick={onInspect}>打开体检与清洗</button>}
      {onRelate&&<button type="button" disabled={disabled} onClick={onRelate}>多表？检查关联与粒度</button>}</div>:null}
    <p className="analysis-start-hint">点击只填入问题，修改后再发送。</p>
  </section>;
}
