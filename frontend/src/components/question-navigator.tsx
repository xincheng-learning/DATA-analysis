"use client";
import {useEffect,useRef} from 'react';
import {ArrowDownToLine,List} from 'lucide-react';

type NavigationMessage={role:string;content:string;metadata?:Record<string,unknown>};
export function QuestionNavigator({messages,onJump,onLatest}:{messages:NavigationMessage[];onJump:(index:number)=>void;onLatest:()=>void}){
  const menu=useRef<HTMLDetailsElement>(null);
  const questions=messages.flatMap((message,index)=>message.role==='user'&&message.metadata?.type!=='upload'&&message.content.trim()?[{index,text:message.content.replace(/\s+/g,' ').trim()}]:[]);
  useEffect(()=>{
    const close=(event:PointerEvent)=>{if(menu.current?.open&&!menu.current.contains(event.target as Node))menu.current.open=false;};
    document.addEventListener('pointerdown',close);
    return()=>document.removeEventListener('pointerdown',close);
  },[]);
  const close=()=>{if(menu.current)menu.current.open=false;};
  if(!questions.length)return null;
  return <details ref={menu} className="question-navigator" onKeyDown={event=>{if(event.key==='Escape'){close();menu.current?.querySelector('summary')?.focus();}}}>
    <summary aria-label={`定位问题，共${questions.length}个提问`} title="打开问题目录"><List size={14}/><span>问题</span><small>{questions.length}</small></summary>
    <nav className="question-directory" aria-label="本聊天问题目录"><header><strong>问题目录</strong><span>{questions.length} 个提问</span></header>
      <div className="question-directory-list">{questions.map((question,ordinal)=><button key={question.index} type="button" data-question-index={question.index} title={question.text} aria-label={`第${ordinal+1}问：${question.text}`} onClick={()=>{close();onJump(question.index);}}><small>{String(ordinal+1).padStart(2,'0')}</small><span>{question.text}</span></button>)}</div>
      <button type="button" className="question-directory-latest" aria-label="回到最新消息" onClick={()=>{close();onLatest();}}><ArrowDownToLine size={14}/>回到最新消息</button>
    </nav>
  </details>;
}
