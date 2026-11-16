"use client";

import {useEffect,useState} from 'react';
import {ResultTable} from './data-panel';
import {workbenchRequest,type WorkbenchTable} from '@/lib/api';

interface TablePage {snapshot:string;table_name:string;columns:string[];row_count:number;offset:number;rows:Record<string,unknown>[]}

export function TableStructureInspector({table,sessionId,snapshot,grain,purpose,independent}:{table:WorkbenchTable;sessionId:string;snapshot:string;grain?:string;purpose?:string;independent?:string}){
  const [tab,setTab]=useState<'structure'|'data'>('structure'),[request,setRequest]=useState({offset:0});
  const [page,setPage]=useState<TablePage>(),[loading,setLoading]=useState(false),[error,setError]=useState('');
  useEffect(()=>{
    if(tab!=='data')return;
    let active=true;
    void workbenchRequest<TablePage>(sessionId,`workbench/tables/${encodeURIComponent(table.table_name)}/rows?snapshot=${encodeURIComponent(snapshot)}&offset=${request.offset}`)
      .then(result=>{if(active){setPage(result);setError('');}})
      .catch(err=>{if(active)setError(err.message);})
      .finally(()=>{if(active)setLoading(false);});
    return()=>{active=false;};
  },[sessionId,snapshot,table.table_name,tab,request]);
  const meta=table.source.relationship_metadata;
  return <section className="table-inspector" aria-label="所选表的结构与数据"><header><div><h3>{table.table_name}</h3><p>{table.source.kind==='postgresql'?`PostgreSQL · ${table.source.schema}.${table.source.table}`:table.filename} · {table.rows.toLocaleString()} 行 · {table.columns.length} 字段</p></div>
    <div className="table-inspector-tabs" role="group" aria-label="表查看方式"><button aria-pressed={tab==='structure'} onClick={()=>setTab('structure')}>结构</button><button aria-pressed={tab==='data'} onClick={()=>{setLoading(true);setTab('data');setRequest({offset:page?.offset||0});}}>数据</button></div></header>
    {(purpose||grain||independent)&&<p className="cleaning-note">{purpose}{grain?` · ${grain}`:''}{independent?` · 可独立分析：${independent}`:''}</p>}
    {tab==='structure'?<div className="table-structure-scroll"><table><thead><tr><th>字段</th><th>数据类型</th><th>声明键</th><th>实际空值数</th></tr></thead><tbody>{table.columns.map(column=><tr key={column}><th scope="row">{column}</th><td><code>{table.dtypes[column]}</code></td><td>{[meta?.primary_key.includes(column)?'PK · 数据库主键':'',meta?.foreign_keys.some(key=>key.columns.includes(column))?'FK · 数据库外键':''].filter(Boolean).join(' / ')||'—'}</td><td>{table.null_counts[column]??'未记录'}</td></tr>)}</tbody></table></div>:
      <><div className="table-data-navigation"><span>{page?`第 ${page.row_count?page.offset+1:0}—${page.offset+page.rows.length} / ${page.row_count.toLocaleString()} 行`:`预览 ${table.preview.length} / ${table.rows.toLocaleString()} 行`}</span>
        <button disabled={loading||!page||page.offset===0} onClick={()=>{setLoading(true);setRequest({offset:page!.offset-50});}}>上一页</button><button disabled={loading||!page||page.offset+50>=page.row_count} onClick={()=>{setLoading(true);setRequest({offset:page!.offset+50});}}>下一页</button>{loading&&<small role="status">读取中…</small>}</div>
        {error&&<p role="alert" className="cleaning-alert">{error}<button onClick={()=>{setLoading(true);setRequest({...request});}}>重试</button></p>}
        <ResultTable key={`${snapshot}:${table.table_name}:${page?.offset??'preview'}`} columns={table.columns} rows={page?.rows||table.preview}/><p className="cleaning-note">当前数据版本，只读查看，每页最多50行；原值、空值和零分别保留。</p></>}
    <p className="cleaning-note">{meta?.status==='declared'?'PK/FK 来自数据库实际声明。':'没有可见的数据库键声明；字段名、唯一值或 AI 建议都不会被标为物理主外键。'}</p>
  </section>;
}
