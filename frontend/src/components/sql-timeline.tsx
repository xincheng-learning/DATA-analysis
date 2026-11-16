"use client";

import { useState } from "react";
import { Check, Copy, Database, CircleCheck, CircleX, Loader2, Clock3 } from "lucide-react";
import { records, record, sqlExecutions, type RecordValue } from "@/lib/analysis";

const statuses: Record<string, string> = { generated: "已生成 · 尚未执行", running: "正在执行", succeeded: "执行成功", failed: "执行失败", blocked: "已拦截", cancelled: "已中断", recorded: "历史查询记录", legacy_failed: "历史尝试未通过" };

function SqlCode({ sql }: { sql: string }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState(false);
  async function copy() {
    try { await navigator.clipboard.writeText(sql); setCopied(true); setError(false); setTimeout(() => setCopied(false), 1800); }
    catch { setError(true); }
  }
  return <div className="sql-code"><div className="sql-code-heading"><span>DUCKDB / SQL</span><button className="icon-button" aria-label="复制 SQL" title={error ? "复制失败，可直接选择代码复制" : "复制 SQL"} onClick={copy}>{copied ? <Check size={13} /> : <Copy size={13} />}</button></div>
    <pre><code>{sql}</code></pre>{error && <p role="alert" className="px-3 pb-2 text-xs">复制失败，可选择代码手动复制。</p>}</div>;
}

export function SqlTimeline({ metadata, running = false }: { metadata: RecordValue; running?: boolean }) {
  const queries = sqlExecutions(metadata);
  const trace = records(record(metadata.evidence).trace);
  const sources = records(metadata.data_sources);
  return <section className="sql-timeline" aria-label="SQL 执行记录">
    <div className="section-heading"><div><h3>SQL 执行过程 <span className="count-badge">{queries.length}</span></h3></div><Database size={17} /></div>
    {!queries.length && <div className="workspace-empty compact"><Database size={24} strokeWidth={1.4} /><p>{running ? "本轮尚未生成 SQL" : ['chat','clarify','refuse'].includes(String(metadata.route)) ? "本轮未执行分析 SQL" : "这条回复没有 SQL 记录"}</p><span>{running ? "只显示当前任务的执行过程" : "历史查询请主动选择对应回复"}</span></div>}
    <details className="sql-details" open={running || undefined}><summary>查看 SQL 与执行过程<span>{queries.length} 条查询</span></summary>
    <ol className="query-list">{queries.map((query, index) => <li key={index} className={`query-entry query-${query.status}`}>
      <div className="query-heading"><span className="query-number">{String(index + 1).padStart(2, "0")}</span>
        {query.status === "running" ? <Loader2 className="animate-spin" size={14} /> : query.status === "succeeded" ? <CircleCheck size={14} /> : ["failed", "blocked", "legacy_failed"].includes(query.status) ? <CircleX size={14} /> : <Clock3 size={14} />}
        <strong>{statuses[query.status] || "查询记录"}</strong><span className="query-duration">{query.seconds == null ? "" : `${query.seconds.toFixed(3)} s`}</span></div>
      {query.question && <p className="query-question">{query.question}</p>}
      <SqlCode sql={query.sql} />
      <div className="query-footer">{query.rowCount != null && <span>返回 {query.rowCount.toLocaleString("zh-CN")} 行</span>}{index > 0 && queries[index - 1].status === "failed" && <span>修正后重试</span>}</div>
      {query.error && <p className="query-error">{query.error}</p>}
      {query.validation && <p className="query-error">结果验证未通过：{query.validation}</p>}
      {query.status === "recorded" && <p className="query-legacy">旧记录缺少完整执行状态，不能还原全部过程。</p>}
    </li>)}</ol>
    {trace.length > 0 && <details className="trace-stages"><summary>完整步骤 · {trace.length} 个阶段</summary><ol>{trace.map((step, index) => <li key={index}><span>{String(step.label || step.node)}{step.status === "running" ? " · 进行中" : ""}</span><span>{Number(step.seconds || 0).toFixed(2)} s</span>{step.error ? <p>{String(step.error)}</p> : null}</li>)}</ol></details>}
    {sources.length > 0 && <details className="trace-sources"><summary>数据来源 · {sources.length} 张表</summary>{sources.map((source, index) => <p key={index}><Database size={12} /><span>{String(source.table_name)} · {Number(source.rows || 0).toLocaleString()} 行</span></p>)}</details>}
    </details>
  </section>;
}
