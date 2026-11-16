// @ts-expect-error Plotly's installed d3-hierarchy dependency ships without TypeScript declarations.
import {hierarchy,pack} from 'd3-hierarchy';
import type {RecordValue} from './analysis';

export function packedBubbles(trace:RecordValue,width:number,height:number) {
  const values=trace.y as number[],labels=trace.x as unknown[];
  const root=hierarchy({children:values.map((value,index)=>({value,index}))}).sum((node:{value?:number})=>node.value||0);
  const side=Math.max(40,Math.min(width,height));
  const offsetX=(width-side)/2,offsetY=(height-side)/2;
  pack().size([side,side]).padding(3)(root);
  const nodes=root.leaves() as {data:{index:number};x:number;y:number;r:number}[];
  if(values.every(value=>value===0))for(const node of nodes){node.x=side/2;node.y=side/2;node.r=0;}
  const ordered=[...nodes].sort((a,b)=>a.data.index-b.data.index);
  return {...trace,x:ordered.map(n=>n.x+offsetX),y:ordered.map(n=>side-n.y+offsetY),
    customdata:labels.map((label,i)=>[String(label),values[i]]),text:labels.map((label,i)=>values[i]>0?String(label):''),textposition:'middle center',
    marker:{size:ordered.map(n=>n.r*2),sizemode:'diameter',sizemin:0,line:{width:1,color:'#ffffff'}},
    hovertemplate:'%{customdata[0]}<br>数值：%{customdata[1]}<extra></extra>',
    meta:{...(trace.meta as RecordValue),packed_side:side}};
}
