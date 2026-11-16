"use client";

import {useEffect,useId,useRef,useState} from 'react';
import {Database} from 'lucide-react';
import type {WorkbenchTable} from '@/lib/api';

export interface TableRelation {
  id:string;left_table:string;right_table:string;left_keys:string[];right_keys:string[];
  state:'confirmed'|'declared'|'suggested'|'unmatched';label:string;
  cardinality?:string;
}

export function TableRelationshipMap({tables,relations,selected,onSelect,onSelectRelation}:{
  tables:WorkbenchTable[];relations:TableRelation[];selected:string;onSelect:(name:string)=>void;onSelectRelation:(id:string)=>void;
}){
  const host=useRef<HTMLDivElement>(null),marker=useId().replace(/:/g,''),[width,setWidth]=useState(1000);
  useEffect(()=>{const observer=new ResizeObserver(([entry])=>setWidth(entry.contentRect.width));if(host.current)observer.observe(host.current);return()=>observer.disconnect();},[]);
  const columns=Math.max(1,Math.min(3,Math.floor(width/330))),cardWidth=250,gap=80;
  const nodes=tables.map(table=>{
    const keys=new Set(relations.flatMap(relation=>relation.left_table===table.table_name?relation.left_keys:relation.right_table===table.table_name?relation.right_keys:[]));
    const fields=table.columns.filter((column,index)=>index<8||keys.has(column));
    return {table,fields,height:76+fields.length*26,x:0,y:0,rowBottom:0};
  });
  let y=38;
  for(let index=0;index<nodes.length;index+=columns){
    const row=nodes.slice(index,index+columns),height=Math.max(...row.map(node=>node.height));
    row.forEach((node,column)=>{node.x=40+column*(cardWidth+gap);node.y=y;node.rowBottom=y+height;});
    y+=height+70;
  }
  const canvasWidth=Math.max(370,columns*(cardWidth+gap)+40),canvasHeight=Math.max(290,y);
  const paths=relations.flatMap(relation=>{
    const left=nodes.find(node=>node.table.table_name===relation.left_table),right=nodes.find(node=>node.table.table_name===relation.right_table);
    if(!left||!right||relation.left_keys.length!==relation.right_keys.length)return [];
    return relation.left_keys.flatMap((key,index)=>{
      const target=relation.right_keys[index],li=left.fields.indexOf(key),ri=right.fields.indexOf(target);
      if(li<0||ri<0)return [];
      const forward=left.x<right.x,sameColumn=left.x===right.x,sx=left.x+(forward||sameColumn?cardWidth:0),tx=right.x+(forward?0:cardWidth),sy=left.y+56+li*26,ty=right.y+56+ri*26;
      const bend=forward?Math.max(38,(tx-sx)/2):-(40+index*5);
      const adjacent=left.y===right.y&&Math.abs(left.x-right.x)===cardWidth+gap;
      const lane=left.y>=right.y?left.y-14-(index%6)*4:left.rowBottom+18+(index%6)*5;
      const exit=sx+(forward?1:-1)*(20+(index%6)*4),entry=tx-(forward?1:-1)*(20+(index%6)*4);
      return [{relation,key,target,index,labelX:sameColumn?sx+36:(sx+tx)/2,labelY:!sameColumn&&!adjacent?lane-6:(sy+ty)/2-6,
        path:sameColumn?`M ${sx} ${sy} C ${sx+48} ${sy}, ${tx+48} ${ty}, ${tx} ${ty}`:adjacent?`M ${sx} ${sy} C ${sx+bend} ${sy}, ${tx-bend} ${ty}, ${tx} ${ty}`:`M ${sx} ${sy} H ${exit} V ${lane} H ${entry} V ${ty} H ${tx}`}];
    });
  });
  return <div className="schema-map" ref={host}>
    <div className="schema-map-legend"><span className="state-declared">数据库声明</span><span className="state-suggested">建议 / 待确认</span><span className="state-confirmed">用户已确认</span><span className="state-unmatched">未形成匹配</span></div>
    <div className="schema-map-scroll" tabIndex={0} aria-label="数据表结构与关系图，可滚动查看"><div className="schema-map-canvas" style={{width:canvasWidth,height:canvasHeight}}>
      <svg width={canvasWidth} height={canvasHeight} aria-label="字段关联连线"><defs><marker id={marker} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke"/></marker></defs>
        {paths.map(item=><g key={`${item.relation.id}:${item.index}`} className={`schema-connection state-${item.relation.state}`}>
          <path className="schema-link-hit" d={item.path} role="button" tabIndex={0} aria-label={`${item.relation.left_table}.${item.key} → ${item.relation.right_table}.${item.target} · ${item.relation.label}`} onClick={()=>onSelectRelation(item.relation.id)} onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();onSelectRelation(item.relation.id);}}}/>
          <path d={item.path} markerEnd={`url(#${marker})`} pointerEvents="none"/><title>{item.relation.label}</title>
          {item.index===0&&<text x={item.labelX} y={item.labelY} textAnchor="middle" className="schema-edge-label" pointerEvents="none">{item.relation.cardinality||({confirmed:'已确认',declared:'声明',suggested:'待确认',unmatched:'未匹配'}[item.relation.state])}</text>}
        </g>)}
      </svg>
      {nodes.map(({table,fields,x,y,height})=><article key={table.table_name} className={`schema-table${table.table_name===selected?' selected':''}`} style={{left:x,top:y,width:cardWidth,height}}>
        <button className="schema-table-heading" aria-pressed={table.table_name===selected} onClick={()=>onSelect(table.table_name)}><Database size={14}/><strong title={table.table_name}>{table.table_name}</strong><small>{table.rows.toLocaleString()} 行</small></button>
        <div className="schema-table-fields">{fields.map(column=>{
          const meta=table.source.relationship_metadata,primary=meta?.primary_key.includes(column),foreign=meta?.foreign_keys.some(key=>key.columns.includes(column));
          return <button key={column} data-table={table.table_name} data-column={column} title={`${column} · ${table.dtypes[column]}`} onClick={()=>onSelect(table.table_name)}><span className="schema-key" title={[primary?'数据库主键声明':'',foreign?'数据库外键声明':''].filter(Boolean).join(' / ')}>{[primary?'PK':'',foreign?'FK':''].filter(Boolean).join('/')}</span><span className="schema-field-name">{column}</span><code>{table.dtypes[column]}</code></button>;
        })}</div><button className="schema-table-footer" onClick={()=>onSelect(table.table_name)}>{table.columns.length>fields.length?`另有 ${table.columns.length-fields.length} 字段 · 查看完整结构`:'查看结构与数据'}<small>{table.source.kind==='postgresql'?'PostgreSQL':'CSV / Excel'}</small></button>
      </article>)}
    </div></div>
    <p className="cleaning-note">表框内是实际字段；虚线仅表示候选。复合键逐字段对应，点击连线查看依据。没有连线的表仍可独立查看与分析。</p>
  </div>;
}
