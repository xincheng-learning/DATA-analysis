"use client";

import { Coins, Activity } from "lucide-react";
import { record, records, sessionUsage, type RecordValue } from "@/lib/analysis";
import type { AnalysisTask } from "@/lib/api";

export function SessionUsage({ messages, progress }: { messages: { role: string; metadata: RecordValue }[]; progress: AnalysisTask | null }) {
  const liveUsage = progress && ["queued", "running", "cancelling"].includes(progress.status) ? progress.usage : undefined;
  const total = sessionUsage(messages, liveUsage);
  const incomplete = total.legacy > 0 || total.missing > 0 || total.unpriced > 0;
  const format = (amount: number) => amount > 0 && amount < 0.000001 ? "< ¥0.000001" : `¥${amount.toFixed(6)}`;
  const calls = messages.filter((message) => message.role === "assistant").flatMap((message) => records(record(record(message.metadata.evidence).usage).calls));
  if (liveUsage) calls.push(...records(liveUsage.calls));
  return <details className="session-usage">
    <summary><Activity size={14} /><span>本会话 <strong>{!total.reported && (total.legacy || total.missing) ? "未记录" : total.total.toLocaleString("zh-CN")}</strong> <span className="usage-unit">Token</span></span><span className="usage-divider" /><Coins size={14} /><span>预估 <strong>{total.cost == null ? "待计量" : format(total.cost)}</strong>{incomplete && <span className="usage-partial">部分记录</span>}</span></summary>
    <div className="usage-popover"><div className="section-heading"><h3>当前会话用量</h3><span className="count-badge">{total.reported} 次已回报</span></div>
      <div className="usage-stats"><div><span>输入 Token</span><strong>{total.input.toLocaleString()}</strong></div><div><span>输出 Token</span><strong>{total.output.toLocaleString()}</strong></div><div><span>人民币预估</span><strong>{total.cost == null ? "暂无价格记录" : format(total.cost)}</strong></div></div>
      <p>按服务商实际返回用量累计，包含路由、查询、调查、图表等模型调用。刷新后从本会话记录恢复。</p>
      {incomplete && <p className="usage-warning">{total.legacy > 0 ? `${total.legacy} 轮旧分析没有用量记录。` : ""}{total.missing > 0 ? `${total.missing} 次调用未返回用量，可能仍计费。` : ""}{total.unpriced > 0 ? `${total.unpriced} 次调用没有已核对的官方人民币价格。` : ""}已知金额不代表完整账单。</p>}
      <p>采用 2026-10-25 核对的官方价格快照。缓存明细缺失按未命中估算；工作日高峰按官方上限估算，节假日实际可能更低。费用以服务商账单为准。</p>
      <a href="https://api-docs.deepseek.com/zh-cn/quick_start/pricing/" target="_blank" rel="noreferrer">查看 DeepSeek 官方价格 ↗</a>
      {calls.length > 0 && <details className="usage-call-list"><summary>每次调用明细 · {calls.length} 次</summary><ol>{calls.map((call, index) => <li key={index}><span>{index + 1}. {String(call.stage || "模型调用")}<small>{String(call.model || "未记录模型")} · {String(record(call.pricing).period === "off_peak" ? "空闲时段" : record(call.pricing).period ? "高峰上限" : "未定价")}</small></span><span>{typeof call.total_tokens === "number" ? `${call.total_tokens.toLocaleString()} Token` : "用量未回报"}<small>{call.cost_cny != null ? format(Number(call.cost_cny)) : "费用未知"}</small></span></li>)}</ol></details>}
    </div>
  </details>;
}
