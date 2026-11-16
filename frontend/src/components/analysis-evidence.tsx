import { ResultTable } from "@/components/data-panel";

type RecordValue = Record<string, unknown>;
const list = (value: unknown): RecordValue[] => Array.isArray(value) ? value : [];
const columns = (value: unknown): string[] => Array.isArray(value) ? value.map(String) : [];
const statuses: Record<string, string> = { completed: "调查结束", partial: '部分完成', clarify: "需要补充信息", inconclusive: "证据不足", blocked: "重复已拦截", budget_exhausted: "预算已用尽", planner_error: "规划失败" };

export function AnalysisScopeNotice({metadata}:{metadata:RecordValue}){
  const scope=(metadata.analysis_scope||{}) as RecordValue,state=(metadata.analysis_scope_state||{}) as RecordValue;
  const historical=!!state.status&&state.status!=='current';
  const changes=columns(state.changed_fields||scope.changed_fields);
  const outcomes:Record<string,string>={complete:'新条件结果已取得。',partial:'新条件仅部分完成，仍有缺口。',failed:'重算失败，尚未取得替代结果。',needs_input:'仍需补资料或确认。',pending:'尚待重算。',cancelled:'重算已停止。'};
  if(!scope.version&&!state.status)return null;
  return <section className="analysis-scope-notice" aria-label="分析适用条件" data-scope-status={String(state.status||'current')}>
    {historical&&<p className="conclusion-warning" role="status"><strong>原条件的历史结果</strong> · {String(state.message||'条件已变化，需按新范围重算。')}</p>}
    {!historical&&!!scope.revision_quote&&<p className="conclusion-warning" role="status">本轮变更：{changes.join('、')||'重新计算'}。{outcomes[String(scope.result_status)]||'结果状态见本轮回执。'}</p>}
    <details className="verification-checks"><summary>{historical?'查看原结果的适用条件':'查看本轮适用条件'}</summary>
      {columns(scope.metric_labels).length>0&&<p>指标：{columns(scope.metric_labels).join('、')}</p>}
      {list(scope.conditions).map((item,index)=><p key={index}>{String(item.text)}</p>)}
      {!list(scope.conditions).length&&<p>本条未记录完整的时间与筛选条件，请结合原问题及执行依据核对。</p>}
      {changes.length>0&&<p>本次变化：{changes.join('、')}</p>}
      <small>条件记录不代表计算或结论已经通过核验。历史数字、图与原始文件保留。</small>
    </details></section>;
}

function QueryEvidence({ item, sessionId }: { item: RecordValue; sessionId?: string }) {
  return <div className="space-y-2">
    {typeof item.source_message_id==='number'&&<p className="text-xs text-muted-foreground">{item.superseded ? `原回复 #${item.source_message_id} 的旧口径结果已被替代，仅留作历史，不进入本轮结论。` : `沿用原回复 #${item.source_message_id} 的已保存结果；当前数据版本一致，本轮未重复执行这一步。`}</p>}
    <p className="text-xs text-muted-foreground">数据版本：{typeof item.data_version === 'string' ? item.data_version.slice(0, 12) : '旧记录未记录版本'}</p>
    {typeof item.sql === "string" && item.sql && <pre className="overflow-auto rounded bg-muted p-2 text-xs whitespace-pre-wrap">{item.sql}</pre>}
    {(!!item.error || !!item.validation_failure) && <p className="text-xs text-destructive">{String(item.error || item.validation_failure)}</p>}
    {Array.isArray(item.rows) && (item.sql||columns(item.columns).length>0)?<><p className="text-xs">实际结果 {Number(item.row_count || 0)} 行，默认展示 50 行</p><ResultTable key={`${sessionId}:${String(item.result_id || item.sql || "")}`} rows={list(item.rows)} columns={columns(item.columns)} totalRows={Number(item.row_count || 0)} sessionId={sessionId} resultId={typeof item.result_id === "string" ? item.result_id : undefined} resultError={typeof item.result_error === "string" ? item.result_error : undefined} /></>:null}
    {list(item.facts).length > 0 && <details className="rounded border p-2"><summary className="cursor-pointer text-xs">数字证据 · {list(item.facts).length} 项 · 引用编号可核对</summary><div className="overflow-auto pt-2"><table className="w-full text-xs text-left"><thead><tr><th>编号</th><th>字段 / 统计</th><th>数值</th><th>范围</th></tr></thead><tbody>{list(item.facts).map((fact, index) => <tr key={index} className="border-t"><td className="p-2">{String(fact.id)}</td><td>{String(fact.label)} {fact.statistic === 'min' ? '最小观测值' : fact.statistic === 'max' ? '最大观测值' : ''}</td><td>{fact.value == null ? '未知' : String(fact.value)}</td><td>{String(fact.scope || '')}</td></tr>)}</tbody></table></div><p className="text-xs text-muted-foreground pt-2">证据对应上方实际 SQL 与数据版本。数值为结果列原单位，未推断币种。</p></details>}
    {list(item.previous_attempts || item.attempts).map((attempt, index) => <div key={index} className="border-l-2 pl-2 text-xs"><p>失败尝试 {index + 1}：{String(attempt.error || "验证未通过")}</p><pre className="whitespace-pre-wrap">{String(attempt.sql || "")}</pre></div>)}
  </div>;
}

export function AnalysisEvidence({ metadata }: { metadata: RecordValue }) {
  const evidence = metadata.evidence as RecordValue | undefined;
  const investigation = metadata.investigation as RecordValue | undefined;
  const catalog = (metadata.metric_context || {}) as RecordValue;
  const synthesis = (investigation?.synthesis || {}) as RecordValue;
  const pythonAttempts = list(metadata.python_attempts);
  if (!metadata.sql && !metadata.python_code && !metadata.python_output && !pythonAttempts.length && !investigation && !evidence?.trace && !metadata.data_sources && !catalog.version) return null;
  return <details className="message-evidence mt-3 rounded border p-3 text-sm">
    <summary className="cursor-pointer font-medium">分析证据 · 查询、结果与执行过程</summary>
    <div className="space-y-3 pt-3">
      {pythonAttempts.length>0&&<details><summary>Python 执行记录 · {pythonAttempts.length} 次</summary>{pythonAttempts.map((attempt,index)=><details key={index} className="mt-2 rounded border p-2"><summary>第 {index+1} 次 · {attempt.error?'失败':'完成'}</summary><pre className="whitespace-pre-wrap text-xs">{String(attempt.code)}</pre>{attempt.error?<pre className="whitespace-pre-wrap text-xs">{String(attempt.error)}</pre>:null}</details>)}</details>}
      {metadata.python_code||metadata.python_output?<section aria-label="Python执行依据"><h3 className="font-medium">Python 代码与工具输出</h3>{metadata.python_code?<details className="answer-details"><summary>查看实际提交的 Python 代码</summary><pre>{String(metadata.python_code)}</pre></details>:null}<p className="text-xs text-muted-foreground">隔离工具输出原文，按本次数据版本保存。</p>{metadata.python_output?<pre className="whitespace-pre-wrap text-xs">{String(metadata.python_output)}</pre>:<p className="text-xs">{metadata.error?String(metadata.error):'工具没有返回文本输出。'}</p>}</section>:null}
      {metadata.route_reason ? <p className="text-xs">路线理由：{String(metadata.route_reason)}</p> : null}
      {list(metadata.data_sources).map((source, index) => { const origin = (source.source || {}) as RecordValue; return <p key={index} className="text-xs text-muted-foreground">数据：{String(source.table_name)}（{String(source.rows)} 行） · {origin.kind === "postgresql" ? `PostgreSQL ${origin.database}.${origin.schema}.${origin.table}` : String(source.filename)} · {origin.imported_at ? new Date(String(origin.imported_at)).toLocaleString("zh-CN") : "未记录读取时间"}</p>; })}
      {catalog.version ? <details className="rounded border p-2"><summary className="cursor-pointer text-xs">指标参照定义 · {String(catalog.version)}</summary><p className="text-xs text-muted-foreground py-2">{String(catalog.validation)} 模板币种与业务筛选仍须确认，当前SQL不自动视为已验证。</p><div className="overflow-auto"><table className="w-full text-xs text-left"><thead><tr><th>指标</th><th>定义</th><th>粒度 / 日期</th><th>模板状态</th></tr></thead><tbody>{list(catalog.definitions).map(item=><tr key={String(item.id)} className="border-t"><td className="p-2">{String(item.label)}</td><td className="p-2 min-w-52">{String(item.definition)}</td><td className="p-2">{String(item.grain)}<br/>{String(item.date_basis)}</td><td className="p-2">{item.template_active?'模板已绑定':'待映射或确认'}</td></tr>)}</tbody></table></div></details> : null}
      <QueryEvidence item={metadata} sessionId={typeof metadata.session_id === "string" ? metadata.session_id : undefined} />
      {investigation && <section className="space-y-3">
        <p className="font-medium">{statuses[String(investigation.status)] || "调查状态"}：{String(investigation.reason)}</p>
        {list(synthesis.evidence).length>0 && <details className="rounded border p-2"><summary className="cursor-pointer text-xs">综合结论引用 · 编号对应步骤与结果</summary><div className="overflow-auto"><table className="w-full text-xs text-left"><thead><tr><th>编号</th><th>程序生成的数字依据</th><th>范围</th></tr></thead><tbody>{list(synthesis.evidence).map(item=><tr key={String(item.id)} className="border-t"><td className="p-2">{String(item.id)}</td><td className="p-2">{String(item.text)}</td><td className="p-2">{String(item.scope)}</td></tr>)}</tbody></table></div><p className="text-xs text-muted-foreground pt-2">S表示下方调查步骤，F表示该步统计事实，R表示结果行；数字沿用该步SQL返回范围。</p></details>}
        {list(investigation.steps).map((step, index) => <details key={index} className="border rounded p-2"><summary className="cursor-pointer">[S{index+1}] 第 {index + 1} 步 · {step.superseded ? "已被新口径替代" : step.status === "succeeded" ? "成功" : "失败"} · {String(step.question)}</summary><p className="text-xs py-2">继续追查的依据：{String(step.reason)}</p><QueryEvidence item={step} sessionId={typeof metadata.session_id === "string" ? metadata.session_id : undefined} /></details>)}
        <p className="text-xs text-muted-foreground">成功与失败步骤均保留，重复步骤会被拦截。回答澄清后可沿用相同数据版本的既有证据，数据变化时重新查询；总步数和调用额度仍受限制。贡献与相关性不代表因果。</p>
      </section>}
      {evidence && <><p className="text-xs">模型调用：{Number(evidence.model_calls || 0)} / {Number(evidence.max_model_calls || 0)} 次</p><ol className="text-xs list-decimal pl-5 space-y-1">{list(evidence.trace).map((step, index) => <li key={index}>{String(step.label)} · {String(step.seconds)} 秒{step.error ? ` · ${step.error}` : ""}</li>)}</ol></>}
    </div>
  </details>;
}
