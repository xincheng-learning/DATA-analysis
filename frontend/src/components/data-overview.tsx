"use client";

import {useState} from 'react';
import type {DataTable,DataGuideTable} from '@/lib/api';
import {ResultTable} from '@/components/data-panel';

export function DataDictionary({tables,nextSteps=[]}:{tables:DataGuideTable[];nextSteps?:string[]}) {
  const sample=(value:unknown)=>value==null?'样例中为空':typeof value==='object'?JSON.stringify(value):String(value);
  return <section className="data-dictionary" aria-label="逐表字段说明">
    <p className="data-guide-intro">按表阅读字段含义、真实样例和完整性。缺失数量基于整张导入表。</p>
    {tables.map((table,index)=><section className="data-guide-table" key={table.table_name} data-table-name={table.table_name}>
      <header><h3>{index+1}. {table.title}<code>{table.table_name}</code></h3><span>{table.rows.toLocaleString()} 行 · {table.fields.length} 个字段</span></header>
      <p>{table.description}</p>
      <div className="markdown-table-scroll"><table><thead><tr><th>字段与类型</th><th>中文含义与用途</th><th>真实样例</th><th>完整性与分析影响</th></tr></thead>
        <tbody>{table.fields.map(field=><tr key={field.column} className={field.missing?'field-has-missing':''} data-column={field.column}>
          <td><code>{field.column}</code><small className="field-type">{field.dtype}</small></td><td><strong>{field.label}</strong><p>{field.meaning}</p></td>
          <td><span className="field-sample" title={sample(field.sample)}>{sample(field.sample)}</span></td>
          <td><span className="field-missing-count">{field.missing_percent==null?'无记录':field.missing?`${field.missing.toLocaleString()} 行（${field.missing_percent.toLocaleString('zh-CN',{maximumFractionDigits:2})}%）`:'0 行'}</span>
            <p>{field.impact||(field.missing?'保留原始记录，空值不代填。':'未检测到NULL空值。')}</p></td>
        </tr>)}</tbody></table></div>
      <details><summary>查看这张表的原始样例 · {table.filename}</summary>
        <ResultTable rows={table.preview} columns={table.fields.map(field=>field.column)}/>
        <p className="inline-source-note">前 {table.preview.length} 行原始样例；这些行可能不包含缺失单元格，缺失统计来自整张表。未知值不当作零。</p>
      </details>
    </section>)}
    <p className="inline-source-note">字段解释不能替代主键和关联关系核验；空值本身不能说明缺失原因或业务事件没有发生。</p>
    {nextSteps.length>0&&<section className="data-guide-next"><h3>后续建议</h3><ul>{nextSteps.map((text,index)=><li key={index}>{text}</li>)}</ul></section>}
  </section>;
}

export function DataOverview({tables}:{tables:DataTable[]}) {
  const [selection,setSelection]=useState(()=>tables.find(table=>Object.values(table.null_counts).some(count=>count>0))?.table_name || tables[0]?.table_name || '');
  const table=tables.find(item=>item.table_name===selection) || tables[0];
  if(!table) return null;
  const missing=Object.entries(table.null_counts).filter(([,count])=>count>0);
  return <section className="inline-data-overview" aria-label="本轮导入数据预览">
    <div className="inline-result-heading"><strong>导入数据概览</strong><span>本轮保存的快照</span></div>
    <div className="markdown-table-scroll"><table className="overview-table"><thead><tr><th>数据表</th><th>记录数</th><th>字段数</th><th>有缺失的字段</th></tr></thead>
      <tbody>{tables.map(item=>{const names=Object.entries(item.null_counts).filter(([,count])=>count>0).map(([name])=>name);return <tr key={item.table_name}>
        <td><code>{item.table_name}</code></td><td>{item.rows.toLocaleString()}</td><td>{item.columns.length}</td>
        <td title={names.join('、')}>{names.length ? names.slice(0,3).join('、')+(names.length>3 ? ` 等${names.length}个字段` : '') : '无'}</td>
      </tr>;})}</tbody></table></div>
    <label className="preview-table-picker">预览数据表<select aria-label="选择预览数据表" value={table.table_name} onChange={event=>setSelection(event.target.value)}>
      {tables.map(item=><option value={item.table_name} key={item.table_name}>{item.table_name}</option>)}
    </select></label>
    {missing.length>0 && <div className="missing-field-summary"><strong>这张表的缺失字段</strong><div className="markdown-table-scroll"><table className="overview-table"><thead><tr><th>字段</th><th>缺失记录数</th><th>占该表比例</th></tr></thead>
      <tbody>{missing.map(([name,count])=><tr key={name}><td><code>{name}</code></td><td>{count.toLocaleString()}</td><td>{table.rows ? `${(count/table.rows*100).toLocaleString('zh-CN',{maximumFractionDigits:2})}%` : '—'}</td></tr>)}</tbody></table></div></div>}
    <ResultTable key={table.table_name} rows={table.preview.slice(0,3)} columns={table.columns.slice(0,6)}/>
    <p className="inline-source-note">导入文件的前 {Math.min(3,table.preview.length)} 行、前 {Math.min(6,table.columns.length)} 列；空值数量基于整张导入表。这是文件预览，不是分析 SQL 的查询结果。</p>
  </section>;
}
