"use client";
import {useEffect,useState} from 'react';
import {createPortal} from 'react-dom';
import {Quote} from 'lucide-react';
import type {QuoteReference} from '@/lib/api';

export function QuoteSelection({onQuote,disabled=false}:{onQuote:(quote:QuoteReference)=>void;disabled?:boolean}) {
  const [selection,setSelection]=useState<{quote:QuoteReference;x:number;y:number}|null>(null);
  useEffect(()=>{
    function inspect(){
      const range=window.getSelection();
      if(disabled||!range||range.isCollapsed||!range.rangeCount){setSelection(null);return;}
      const parent=(node:Node|null)=>node instanceof Element?node:node?.parentElement;
      const start=parent(range.anchorNode)?.closest('[data-quote-message]'),end=parent(range.focusNode)?.closest('[data-quote-message]');
      const text=range.toString().trim(),id=Number(start?.getAttribute('data-quote-message'));
      if(!start||start!==end||!id||!text||text.length>4000){setSelection(null);return;}
      const box=range.getRangeAt(0).getBoundingClientRect();
      setSelection({quote:{message_id:id,text},x:Math.max(12,Math.min(window.innerWidth-110,box.left+box.width/2-36)),y:Math.max(8,box.top-38)});
    }
    document.addEventListener('selectionchange',inspect);return()=>document.removeEventListener('selectionchange',inspect);
  },[disabled]);
  if(!selection)return null;
  return createPortal(<button className="quote-selection-action" style={{left:selection.x,top:selection.y}} onPointerDown={event=>event.preventDefault()} onClick={()=>{onQuote(selection.quote);window.getSelection()?.removeAllRanges();setSelection(null);}}><Quote size={13}/>引用</button>,document.body);
}
