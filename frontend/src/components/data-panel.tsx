"use client";

import { useState } from "react";
import type { DataTable } from "@/lib/api";
import { getResultRows,resultExportUrl } from "@/lib/api";
import {businessLabel,dataLimitsText} from '@/lib/analysis';

export function ResultTable({ rows, columns, totalRows = rows.length, sessionId, resultId, resultError,formatValue }: {
  rows: Record<string, unknown>[]; columns: string[]; totalRows?: number; sessionId?: string; resultId?: string; resultError?: string;
  formatValue?:(value:unknown,column:string,row:Record<string,unknown>)=>string|undefined;
}) {
  const [displayRows, setDisplayRows] = useState(rows);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  async function loadMore() {
    if (loading || !sessionId || !resultId) return;
    setLoading(true); setError("");
    try {
      const page = await getResultRows(sessionId, resultId, displayRows.length);
      if (!page.rows.length) throw new Error("结果分页为空，未追加数据，请检查保存的结果。");
      setDisplayRows((previous) => [...previous, ...page.rows]);
    } catch (error) { setError(error instanceof Error ? error.message : "更多结果读取失败，请重试。"); }
    finally { setLoading(false); }
  }
  if (!columns.length) return <p className="text-xs text-muted-foreground">没有结果列。</p>;
  const numericColumns = new Set(columns.filter(name => displayRows.some(row => typeof row[name] === 'number') && displayRows.every(row => row[name] == null || typeof row[name] === 'number')));
  return <div><div className="result-table overflow-auto max-h-72 rounded border"><table className="text-xs w-full text-left">
    <thead><tr>{columns.map((name) => <th key={name} title={name} className={`p-2 whitespace-nowrap ${numericColumns.has(name) ? 'text-right' : ''}`}>{businessLabel(name)}</th>)}</tr></thead>
    <tbody>{displayRows.map((row, index) => <tr key={index} className="border-t">{columns.map((name) => {
      const value = row[name];
      const original = value == null ? undefined : typeof value === 'object' ? JSON.stringify(value) : String(value);
      const displayed = formatValue?.(value,name,row) ?? (typeof value === 'number' ? value.toLocaleString('zh-CN', {maximumSignificantDigits:12})
        : typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T00:00:00(?:\.0+)?Z?$/.test(value) ? value.slice(0,10) : original);
      return <td key={name} title={original} className={`p-2 max-w-64 break-words ${numericColumns.has(name) ? 'text-right' : ''}`}>{value == null ? <span className="text-muted-foreground">空值</span> : displayed}</td>;
    })}</tr>)}</tbody>
  </table>{!displayRows.length && <p className="p-2 text-xs">查询返回 0 行。</p>}</div>
    {totalRows > rows.length && <div className="result-pagination"><span>已展示 {displayRows.length.toLocaleString()} / {totalRows.toLocaleString()} 行</span>{displayRows.length < totalRows && resultId && sessionId ? <button onClick={loadMore} disabled={loading}>{loading ? "读取中…" : `再展开 ${Math.min(50, totalRows - displayRows.length)} 行`}</button> : displayRows.length < totalRows ? <span>{resultError || "旧结果只保存了前 50 行，无法继续展开。"}</span> : <span>已全部展开</span>}</div>}
    {sessionId&&resultId&&!resultError&&<><a className="report-source-link" href={resultExportUrl(sessionId,resultId)} download>下载完整结果 · {totalRows} 行 CSV</a><small className="result-export-scope">包含本次查询的全部结果行；受原查询筛选和排名范围约束，不等于整份原始数据。</small></>}
    {error && <p role="alert" className="p-2 text-xs text-destructive">{error}</p>}
  </div>;
}

export function DataPanel({ tables, error, expanded }: { tables: DataTable[]; error: string; expanded?:boolean }) {
  if (error) return <p role="alert" className="px-4 py-2 text-sm text-destructive">{error}</p>;
  if (!tables.length) return null;
  return <details className="data-panel" open={expanded}>
    <summary className="cursor-pointer">已连接 {tables.length} 张表 · 查看数据</summary>
    <div className="space-y-2 max-h-80 overflow-auto py-3">{tables.map((table) => <details key={table.table_name} className="table-preview">
      <summary>{table.filename}<span>{table.rows.toLocaleString()} 行 · {table.columns.length} 个字段</span></summary>
      <details className="field-dictionary"><summary>字段、缺失值与来源</summary>
      <p className="text-xs text-muted-foreground">{table.source.kind === "postgresql" ? `PostgreSQL 快照：${table.source.database}.${table.source.schema}.${table.source.table}` : "本地文件"} · {table.source.imported_at ? new Date(table.source.imported_at).toLocaleString("zh-CN") : "旧数据未记录读取时间"}</p>
      <table className="field-table"><thead><tr><th>字段</th><th>类型</th><th>缺失数</th></tr></thead><tbody>{table.columns.map(column=><tr key={column}><td>{column}</td><td>{table.dtypes[column]}</td><td>{table.null_counts[column] ?? 0}</td></tr>)}</tbody></table></details>
      <ResultTable rows={table.preview} columns={table.columns} />
      <p className="text-xs text-muted-foreground">最多预览 5 行。Excel 默认读取第一个工作表；数据库为导入时的快照，更新后需重新导入。</p>
    </details>)}</div><details className="data-limits"><summary>数据规模、分页与下载范围</summary><p>{dataLimitsText}</p></details>
  </details>;
}
