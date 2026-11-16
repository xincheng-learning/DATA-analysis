export type RecordValue = Record<string, unknown>;
export const records = (value: unknown): RecordValue[] => Array.isArray(value) ? value.filter((item) => item && typeof item === "object") : [];
export const record = (value: unknown): RecordValue => value && typeof value === "object" ? value as RecordValue : {};
export const dataLimitsText='文件：单次最多5个，每个10 MiB；每表最多50万行、200列。数据库导入：每次最多5表，每表5万行。查询结果上限10万行或32 MiB，超过会停止，原表保留；请先汇总或明确缩小范围，不能把截取结果当全量。图表来源最多5000行。聊天通常只预览前50行；完整结果保存后每次再读50行，CSV下载包含该次查询的完整结果，不是原始数据全量。';

export function withScopeHistory<T extends {id?:number|string|null;role:string;metadata:RecordValue}>(messages:T[],pendingScope?:RecordValue|null):T[]{
  const replacements=new Map<number,RecordValue>();
  for(const message of messages){
    const scope=record(message.metadata.analysis_scope);
    if(Array.isArray(scope.revises_message_ids))for(const mid of scope.revises_message_ids){
      if(typeof mid==='number')replacements.set(mid,{status:'historical_scope',changed_fields:scope.changed_fields||[],
        replacement_message_id:message.id,recalculation_status:scope.result_status||'pending',target_conditions:scope.conditions||[]});
    }
  }
  if(Array.isArray(pendingScope?.revises_message_ids))for(const mid of pendingScope.revises_message_ids){
    if(typeof mid==='number')replacements.set(mid,{status:'historical_scope',changed_fields:pendingScope.changed_fields||[],recalculation_status:'pending',target_conditions:pendingScope.conditions});
  }
  const outcomes:Record<string,string>={complete:'新条件的结果已取得。',partial:'新条件分析仅部分完成，仍有缺口。',
    failed:'新条件重算失败，尚未取得替代结果。',needs_input:'新条件仍需补充资料或确认。',pending:'新条件尚待重算。',cancelled:'新条件重算已停止。'};
  return messages.map(message=>{
    const state=typeof message.id==='number'?replacements.get(message.id):undefined;
    const fields=Array.isArray(state?.changed_fields)?state.changed_fields.map(String):[];
    const oldPeriod=records(record(message.metadata.analysis_scope).conditions).find(item=>item.key==='period')?.text;
    const newPeriod=records(state?.target_conditions).find(item=>item.key==='period')?.text;
    if(state&&Array.isArray(state.target_conditions)&&oldPeriod!==newPeriod&&!fields.includes('时间范围'))fields.push('时间范围');
    return state?{...message,metadata:{...message.metadata,analysis_scope_state:{...state,changed_fields:fields,
      message:'本条数字、图和结论保留于原条件，不可直接用于变更后的分析。'+(outcomes[String(state.recalculation_status)]||outcomes.pending)}}}:message;
  });
}

export function resultSources(metadata:RecordValue,path:(string|number)[]=[]):{metadata:RecordValue;sourcePath:(string|number)[];stepId?:string}[]{
  const workflow=records(record(metadata.workflow).steps),investigation=records(record(metadata.investigation).steps);
  if(workflow.length)return workflow.flatMap((step,index)=>step.status==='succeeded'&&!step.superseded?
    resultSources(record(step.result),[...path,'workflow','steps',index,'result']):[]);
  if(investigation.length)return investigation.flatMap((step,index)=>step.status==='succeeded'&&!step.superseded?
    resultSources(step,[...path,'investigation','steps',index]):[]);
  return [{metadata,sourcePath:path}];
}

export function reportSources(metadata:RecordValue) {
  const available=resultSources(metadata);
  return records(record(metadata.analysis_report).sources).map(source=>{
    let value:unknown=metadata;
    const path=Array.isArray(source.path)?source.path as (string|number)[]:[];
    for(const part of path)value=typeof part==='number'&&Array.isArray(value)?value[part]:record(value)[String(part)];
    const data=record(value),workflow=records(record(metadata.workflow).steps);
    const workflowSource=path[0]==='workflow'&&path.length===4;
    return {key:String(source.key),label:String(source.label||'分析结果'),metadata:data,table:record(source.table),
      scope:String(source.scope||''),hasChart:records(record(data.chart_spec).data).length>0,
      stepId:workflowSource?String(workflow[Number(path[2])]?.id):undefined,sourcePath:path,
      canAdd:available.some(item=>JSON.stringify(item.sourcePath)===JSON.stringify(path))};
  });
}

export interface SqlExecution {
  sql: string;
  status: string;
  seconds?: number;
  rowCount?: number;
  error?: string;
  validation?: string;
  question?: string;
}

export function sqlExecutions(metadata: RecordValue): SqlExecution[] {
  const trace = records(record(metadata.evidence).trace);
  const queries: SqlExecution[] = [];
  let pending: SqlExecution | undefined;
  for (const event of trace) {
    if (event.node === "write_sql" && typeof event.sql === "string" && event.sql) {
      pending = { sql: event.sql, status: "generated" };
      queries.push(pending);
    } else if (event.node === "execute_sql") {
      const sql = typeof event.sql === "string" && event.sql ? event.sql : pending?.sql;
      if (!sql) continue;
      const query = pending?.sql === sql ? pending : { sql, status: "recorded" };
      if (query !== pending) queries.push(query);
      query.status = String(event.status || (event.error ? "failed" : "succeeded"));
      query.seconds = typeof event.seconds === "number" ? event.seconds : undefined;
      query.rowCount = typeof event.row_count === "number" ? event.row_count : undefined;
      query.error = event.error ? String(event.error) : undefined;
      query.question = typeof event.question === "string" ? event.question : undefined;
      pending = undefined;
    } else if (event.node === "validator" && event.validation_failure && queries.length) {
      queries[queries.length - 1].validation = String(event.validation_failure);
    }
  }
  if (queries.length) return queries;
  const steps = records(record(metadata.investigation).steps);
  for (const step of steps.length ? steps : [metadata]) {
    for (const attempt of records(step.previous_attempts || step.attempts)) {
      if (attempt.sql) queries.push({ sql: String(attempt.sql), status: "legacy_failed", error: String(attempt.error || "验证未通过") });
    }
    if (step.sql) queries.push({ sql: String(step.sql), status: "recorded", rowCount: typeof step.row_count === "number" ? step.row_count : undefined,
      question: typeof step.question === "string" ? step.question : undefined, error: step.error ? String(step.error) : undefined });
  }
  return queries;
}

export function sessionUsage(messages: { role: string; metadata: RecordValue }[], liveUsage?: RecordValue) {
  const usages = messages.filter((message) => message.role === "assistant").map((message) => record(record(message.metadata.evidence).usage));
  const legacy = messages.filter((message) => message.role === "assistant" && !message.metadata.cleaning_event && (message.metadata.route || message.metadata.evidence)
    && !record(record(message.metadata.evidence).usage).calls).length;
  if (liveUsage) usages.push(liveUsage);
  const sum = (key: string) => usages.reduce((total, item) => total + (typeof item[key] === "number" ? item[key] as number : 0), 0);
  const priced = usages.filter((item) => item.cost_cny != null);
  return { input: sum("input_tokens"), output: sum("output_tokens"), total: sum("total_tokens"), reported: sum("reported_calls"),
    missing: sum("missing_usage_calls"), unpriced: sum("unpriced_calls"), legacy,
    cost: priced.length ? priced.reduce((total, item) => total + Number(item.cost_cny), 0) : null };
}
import type { AnalysisTask } from "./api";


export function appendTaskResult(messages: { role: "user" | "assistant"; content: string; metadata: RecordValue }[], task: AnalysisTask) {
  if (messages.some((message) => message.metadata.task_id === task.task_id)) return messages;
  const result = task.result;
  const evidence = record(result?.evidence);
  const usage = task.usage || evidence.usage;
  const question = result?.question;
  const last = messages.at(-1);
  const previous = question && !(last?.role === "user" && last.content === question)
    ? [...messages, { role: "user" as const, content: question, metadata: {} }] : messages;
  const received=result?.answer||task.draft_answer;
  const content = task.status === "succeeded" && result?.answer ? result.answer
    : task.status === "cancelled" ? [received,"已停止后续分析；已发出的模型请求可能仍计费。"].filter(Boolean).join('\n\n')
    : [task.error || "任务未能完成，请查看已记录的执行证据。", received].filter(Boolean).join('\n\n');
  return [...previous, { role: "assistant" as const, content, metadata: {
    ...result, task_id: task.task_id, ...(task.status === "failed" ? { error: task.error || content } : {}),
    evidence: { ...evidence, trace: task.trace, model_calls: task.model_calls, max_model_calls: task.max_model_calls,
      ...(usage ? { usage } : {}) },
  } }];
}

export function reconcileTaskHistory(messages: { role: "user" | "assistant"; content: string; metadata: RecordValue }[], task: AnalysisTask) {
  const saved = messages.some(message => message.role === 'assistant' && record(message.metadata.evidence).task_id === task.task_id);
  return { saved, messages: saved ? messages : appendTaskResult(messages, task) };
}
const businessLabels: Record<string, string> = {
  amount:'金额',origin:'来源',destination:'去向',
  avg_item_amount_per_order:'每单商品金额',average_item_amount_per_order:'每单商品金额',
  avg_order_item_amount: '客单商品金额',
  average_order_item_amount: '客单商品金额',
  item_amount: "商品金额", product_amount: "商品金额", payment_amount: "支付金额", freight_amount: "运费",
  item_amount_sum: '商品金额合计', null_item_amount_rows:'商品金额为空的订单数',orders_with_known_item_amount:'金额已知的订单数',
  order_count: "订单数", delivered_orders: "已送达订单数", customer_count: "购买客户数", repeat_customers: "复购客户数",
  repeat_rate: "复购率", retention_rate: "留存率", late_rate: "延期率", conversion_rate: "转化率",
  month_offset: "距首购月数", quarter_offset: "距首购季度数", cohort: "首购群组", cohort_month: "首购月份",
  customer_state: "客户州", month: "月份", revenue: "销售额", sales: "销售额",
  seller_state:'卖家州',buyer_state:'买家州',net_sales:'净销售额',
  purchase_month:'下单月份',row_type:'记录类型',change:'变动',active_row:'原表行号',
  order_purchase_timestamp:'下单时间',order_approved_at:'审核时间',order_delivered_carrier_date:'交给物流时间',order_delivered_customer_date:'实际送达时间',
  purchase: "下单", placed: "下单", approved: "审批", shipped: "发货", delivered: "签收",
};

export function businessLabel(value: string): string {
  const key=value.toLowerCase(),currency=key.match(/_(brl|cny|usd|eur)$/)?.[1];
  const months=['january','february','march','april','may','june','july','august','september','october','november','december'];
  const month=months.findIndex(name=>key.startsWith(name+'_'));
  if(month>=0){const metric=key.slice(months[month].length+1);if(businessLabels[metric.replace(/_(brl|cny|usd|eur)$/,'')])return `${month+1}月${businessLabel(metric)}`;}
  const label=businessLabels[key.replace(/_(brl|cny|usd|eur)$/, '')];
  return label?label+(currency?`（${currency.toUpperCase()}）`:''):value;
}

export function compactAnswer(content: string, metadata: RecordValue) {
  const synthesis=record(record(metadata.investigation).synthesis);
  if (!metadata.error && !metadata.validation_failure && synthesis.status==='generated' && records(synthesis.findings).length) {
    const cards=records(synthesis.evidence),seen=new Set<string>();
    const planned=records(synthesis.findings);
    const covered=new Set(planned.flatMap(finding=>Array.isArray(finding.evidence_ids)?finding.evidence_ids.map(String):[]));
    const missingChanges=cards.filter(card=>card.kind==='change'&&!covered.has(String(card.id))).slice(0,8);
    const groups=missingChanges.length?[{text:'本次查询的指标变化',evidence_ids:missingChanges.map(card=>card.id)},...planned]:planned;
    const coverageText=(value:unknown)=>String(value).replace(/该(月份|期间)数据覆盖不完整/g,'该$1可能覆盖不完整');
    const findings=groups.map(finding=>{
      const references=Array.isArray(finding.evidence_ids)?finding.evidence_ids:[];
      const linked=references.flatMap(id=>cards.filter(card=>card.id===id));
      const statistics=linked.filter(card=>card.kind!=='row');
      const selected=(statistics.length?statistics:linked.slice(0,2)).filter(card=>!seen.has(String(card.id)));
      selected.forEach(card=>seen.add(String(card.id)));
      const text=[String(finding.text),...selected.map(card=>`[${card.id}] ${card.text}`)].join(' ');
      return coverageText(text.replace(/\b[a-z][a-z_]+\b/g,businessLabel));
    });
    return {findings,warnings:[...(Array.isArray(synthesis.warnings)?synthesis.warnings.map(coverageText):[]),'仅依据当前导入数据和本次查询，不能据此推断全平台走势；统计贡献不证明因果。'],
      hypotheses:Array.isArray(synthesis.hypotheses)?synthesis.hypotheses.map(String):[],nextChecks:Array.isArray(synthesis.next_checks)?synthesis.next_checks.map(String):[],details:content};
  }
  if (metadata.error || metadata.validation_failure || !content.includes("主要发现\n")) return null;
  const main = content.split("主要发现\n").at(-1)!.split(/\n\n数字依据/)[0];
  const readable = (text: string) => text.replace(/\b[a-z][a-z_]+\b/g, businessLabel)
    .replace(/的最大观测值为/g, "最高为").replace(/最小观测值为/g, "最低为")
    .replace(/，对应 /g, "，").replace(/月份=/g, "月份 ");
  const lines = main.split("\n").map(line => readable(line.trim())).filter(Boolean);
  const warnings = lines.filter(line => /不完整|未满观察期|不能|无可用|没有可用|未知/.test(line));
  let findings = lines.filter(line => !warnings.includes(line));
  if (findings.length>1) findings=findings.filter(line=>!/^对比 \d+ 个首购群组，展示购买留存/.test(line));
  const traces=records(record(metadata.chart_spec).data);
  if (main.includes('最大观测值') && traces.length>0 && traces.every(trace=>trace.type==='scatter' && String(trace.mode).includes('lines'))) {
    const trends=traces.flatMap(trace=>{
      const xs=Array.isArray(trace.x) ? trace.x : [], ys=Array.isArray(trace.y) ? trace.y : [];
      if (xs.length<2 || xs.length!==ys.length || !xs.every((value,index)=>/^\d{4}-\d{2}/.test(String(value)) && (index===0 || String(value)>=String(xs[index-1]))) || !ys.every(value=>typeof value==='number' && Number.isFinite(value))) return [];
      const first=ys[0] as number, last=ys.at(-1) as number, change=last-first;
      const number=(value:number)=>value.toLocaleString('zh-CN',{maximumSignificantDigits:12});
      const peak=Math.max(...ys as number[]), date=String(xs[ys.indexOf(peak)]).slice(0,7);
      return [`图中${businessLabel(String(trace.name || '该指标'))}从 ${number(first)} 变为 ${number(last)}，${change>=0?'增加':'减少'} ${number(Math.abs(change))}${first>0 ? `（${number(Math.abs(change/first)*100)}%）` : ''}；${date} 最高，为 ${number(peak)}。`];
    });
    if (trends.length) findings=[...trends,...findings];
  }
  if (content.includes("仅依据已保留的")) warnings.push("本次结论只依据已保留的结果，不能代表全量数据。");
  if (metadata.chart_error) warnings.push("图表生成失败，查询结果仍可查看。");
  if (record(metadata.investigation).status === "partial") warnings.push("调查部分完成，尚有步骤未成功。");
  if (/以下结果字段存在空值/.test(content) && !warnings.some(line=>line.includes('空白'))) warnings.push("部分结果为空值；是否为不适用或数据缺失，需结合计算口径核对。");
  return { findings, warnings, hypotheses:[] as string[], nextChecks:[] as string[], details: content };
}
