/** Map existing AskResponse fields into user-visible delivery trust layers. */

export type TrustTone = 'ok' | 'partial' | 'warn' | 'fail' | 'unknown';

export type TrustLayer = {
  id: 'intent' | 'plan' | 'calculation' | 'business';
  label: string;
  status: string;
  tone: TrustTone;
};

export type DeliveryTrust = {
  layers: TrustLayer[];
  unverifiedWarning?: string;
};

type Check = { id?: string; status?: string; detail?: string };
type RecordValue = Record<string, unknown>;

/** Reuse saved preparation/decisions; never rerun checks to render a delivery. */
export function deliveryWorkflow(metadata:RecordValue={},report?:RecordValue|null){
  const records:RecordValue[]=[];
  function collect(value:RecordValue){
    records.push(value);
    list((value.workflow as RecordValue|undefined)?.steps).filter(step=>!step.superseded).forEach(step=>collect(step.result as RecordValue||{}));
    list((value.investigation as RecordValue|undefined)?.steps).filter(step=>!step.superseded).forEach(collect);
  }
  collect(metadata);
  const contexts=records.map(value=>((value.analysis_process as RecordValue|undefined)?.preparation||{}) as RecordValue)
    .filter(value=>!!value.readiness);
  const preparation=contexts.flatMap(context=>list((context.readiness as RecordValue).requirements));
  const declarations=[...new Set(records.flatMap(value=>list(((value.analysis_brief||report?.brief||{}) as RecordValue).scope_sources))
    .filter(source=>typeof source.text==='string').map(source=>String(source.text)))];
  const coverage=list(report?.coverage||((metadata.analysis_process||{}) as RecordValue).coverage);
  return {contexts,preparation,declarations,coverage,
    pending:preparation.filter(item=>['needs_decision','needs_input','blocked'].includes(String(item.status)))};
}

export function deliveryOutcome(metadata:RecordValue={},report?:RecordValue|null):{status:string;label:string;detail:string}|null{
  const history=(metadata.analysis_scope_state||{}) as RecordValue;
  if(history.status&&history.status!=='current')return {status:'historical',label:'历史结果 · 当前条件需重算',
    detail:String(history.message||'数字、图和结论仅适用于原条件，请按当前数据与口径重算。')};
  const preparation=deliveryWorkflow(metadata,report).preparation;
  const sources=deliverySources(metadata,report);
  const available=sources.some(source=>!source.error&&!source.validation_failure&&Array.isArray(source.columns)&&source.columns.length>0&&
    (Array.isArray(source.rows)||source.kind==='execution_receipt'));
  const flow=(metadata.workflow||metadata.investigation||{}) as RecordValue;
  const scope=(metadata.analysis_scope||{}) as RecordValue;
  const brief=(metadata.analysis_brief||report?.brief||{}) as RecordValue;
  if(scope.result_status==='cancelled'||['cancelled','stopped'].includes(String(flow.status))||metadata.task_status==='cancelled')return {status:'stopped',label:'已停止 · 未宣称全部完成',detail:'已收到的内容和已完成结果保留。可在当前聊天补充条件后继续；已发出的请求可能仍计费。'};
  if(preparation.some(item=>item.status==='blocked')||list(metadata.checks).some(check=>['analysis_contract','business_definition'].includes(String(check.id))&&check.status==='failed'))return {status:available?'partial':'failed',label:available?'部分完成 · 部分准备未通过':'执行准备未通过',detail:'内部计划或数据准备未通过的部分尚未完成；原因见下方，不要求用户补填内部字段。'};
  if(metadata.route==='refuse')return {status:available?'partial':'refused',label:available?'部分完成 · 其余已拒绝':'本次请求已拒绝',detail:'请按回复中的原因调整问题；已有历史结果保留，不把拒绝算成分析完成。'};
  if(flow.status==='waiting')return {status:available?'partial':'needs_input',label:'等待确认 · 原任务已保留',detail:'在下方方案中确认后继续；待确认步骤及其后续操作尚未执行。'};
  const stepFailed=list(flow.steps).some(step=>!step.superseded&&['failed','skipped','cancelled'].includes(String(step.status)));
  if(metadata.error||metadata.validation_failure||metadata.chart_error||stepFailed){
    return available?{status:'partial',label:'部分完成',detail:metadata.chart_error?'数据已算出，图表尚未完成。可先查看或下载可用数据，再按下方原因继续。':'已完成部分保留；仍有步骤未完成，请按下方说明补资料、修改条件或恢复所需环境后继续。'}:
      {status:'failed',label:'本轮未完成',detail:'本轮尚未取得可交付结果。请查看失败说明，修正条件或恢复所需环境后在当前聊天继续。'};
  }
  const needs=preparation.some(item=>['needs_input','needs_decision'].includes(String(item.status)))||!!metadata.clarification||list(brief.metric_dependencies).some(item=>item.status==='needs_input')||
    list(brief.acceptance).some(item=>!!item.intent_gap)||list(report?.coverage).some(item=>item.status==='needs_input'||item.status==='unanswered');
  if(needs)return {status:available?'partial':'needs_input',label:available?'部分完成 · 尚需确认':'需补资料或确认',detail:'补充下方缺少的资料或确认口径后继续；已完成结果保留，不必新建聊天。'};
  if(flow.status==='partial'||report?.status==='partial')return {status:'partial',label:'部分完成',detail:'本轮仍有未回答或未核对的要求，具体缺口见下方；可以继续补充条件。'};
  if(available&&preparation.some(item=>item.status==='limited'))return {status:'limited',label:'已计算 · 适用范围受限',detail:'已有结果保留；输入覆盖或任务准备仍有限制，不能直接当作完整业务结论。限制见下方数据准备与核验范围。'};
  if(available)return {status:'computed',label:'已返回计算结果',detail:'计算结果已取得；是否符合业务定义与结论是否成立，仍以各层核验范围为准。'};
  if(metadata.route==='python'&&metadata.python_output)return {status:'partial',label:'已执行 · 缺少结构化结果',detail:'已保留工具输出，但尚无可核对的结果表；不能据此认定统计、图表或导出均已完成。'};
  if(flow.status==='completed')return {status:'completed',label:'操作已完成',detail:'具体产物与执行范围见本轮记录。'};
  return null;
}

function list(value: unknown): RecordValue[] {
  return Array.isArray(value) ? value.filter((item): item is RecordValue => !!item && typeof item === 'object') : [];
}

export function continuationDrafts(metadata:RecordValue,goal:string,report?:RecordValue|null){
  const flow=(metadata.workflow||{}) as RecordValue;
  if(!goal||metadata.streaming||metadata.clarification||flow.status==='waiting'||metadata.route==='cleaning'||metadata.sql_proposal)return [];
  const outcome=deliveryOutcome(metadata,report);
  if(!outcome)return [];
  const original=`原任务：${goal}`;
  if(outcome.status==='refused'||outcome.status==='needs_input')return [{label:'补充或调整问题',text:`${original}\n补充或调整：`}];
  if(['partial','failed','stopped'].includes(outcome.status))return [{label:'继续未完成部分',text:`${original}\n请结合当前数据与已有执行记录继续未完成部分，保留已确认的条件；不要重复执行已完成的写入操作。若缺少资料或环境仍不可用，请明确告诉我。`}];
  if(outcome.status==='computed'||outcome.status==='limited')return [{label:'修改条件并重算',text:`${original}\n保留其余已确认条件，按以下修改重新计算，并标明旧结果的适用范围。\n我要修改：`}];
  return [];
}

export function canResumeDelivery(metadata:RecordValue){
  if(!metadata.data_version||metadata.streaming)return false;
  const history=(metadata.analysis_scope_state as RecordValue|undefined)?.status;
  if(history&&history!=='current')return false;
  const flow=(metadata.workflow||{}) as RecordValue;
  return !!(flow.plan&&['partial','stopped','cancelled'].includes(String(flow.status))||
    !metadata.workflow&&metadata.chart_error&&metadata.result_id&&Array.isArray(metadata.columns)&&metadata.columns.length&&!metadata.error&&!metadata.validation_failure);
}

export function deliverySources(metadata?: RecordValue | null, report?: RecordValue | null): RecordValue[] {
  const sources: RecordValue[] = [];
  function collect(value: RecordValue) {
    const workflow = list((value.workflow as RecordValue | undefined)?.steps);
    const investigation = list((value.investigation as RecordValue | undefined)?.steps);
    if (workflow.length) {
      workflow.filter(step => !step.superseded).forEach(step => collect(step.result as RecordValue || {}));
    } else if (investigation.length) {
      investigation.filter(step => !step.superseded).forEach(collect);
    } else if ((Array.isArray(value.rows) && value.rows.length) || Number(value.row_count) > 0 ||
      value.sql || value.python_code || list((value.chart_spec as RecordValue | undefined)?.data).length ||
      list(value.checks).some(check => check.id === 'calculation_contract')) {
      sources.push(value);
    }
  }
  if (metadata) collect(metadata);
  return sources.length ? sources : list(report?.evidence).filter(card => card.kind === 'execution_receipt');
}

export function currentMetricIds(metadata?: RecordValue | null, report?: RecordValue | null): string[] {
  const sources = deliverySources(metadata, report);
  const briefs = [(metadata?.analysis_brief || report?.brief || {}) as RecordValue,
    ...sources.map(source => (source.analysis_brief || {}) as RecordValue)];
  return [...new Set([
    ...briefs.flatMap(brief => list(brief.acceptance).flatMap(spec => Array.isArray(spec.metric_ids) ? spec.metric_ids.map(String) : [])),
    ...briefs.flatMap(brief => list(brief.metric_dependencies).filter(item => typeof item.metric_id === 'string').map(item => String(item.metric_id))),
    ...[...sources, ...list(report?.evidence).filter(card => card.kind === 'execution_receipt')]
      .flatMap(source => Array.isArray(source.metric_ids) ? source.metric_ids.map(String) : []),
  ])];
}

export function currentMetricDependencies(metadata?: RecordValue | null, report?: RecordValue | null): RecordValue[] {
  const briefs = [(metadata?.analysis_brief || report?.brief || {}) as RecordValue,
    ...deliverySources(metadata, report).map(source => (source.analysis_brief || {}) as RecordValue)];
  return briefs.flatMap(brief => list(brief.metric_dependencies));
}

function intentLayer(report: RecordValue | null | undefined): TrustLayer {
  const coverage = list(report?.coverage);
  const reviewed = report?.acceptance_version === 1;
  if (!coverage.length) {
    return { id: 'intent', label: '问题覆盖', status: report ? '有报告，未逐项核对' : '未评估', tone: 'unknown' };
  }
  if (!reviewed) {
    return { id: 'intent', label: '问题覆盖', status: '历史记录，未按当前规则验收', tone: 'unknown' };
  }
  const answered = coverage.filter((item) => item.status === 'answered').length;
  const needs = coverage.filter((item) => item.status === 'needs_input' || item.status === 'unanswered').length;
  const linked = coverage.filter((item) => item.status === 'linked').length;
  if (answered === coverage.length) {
    return { id: 'intent', label: '问题覆盖', status: `已回答 ${answered}/${coverage.length}（按本轮拆解）`, tone: 'ok' };
  }
  if (needs || linked || answered) {
    return {
      id: 'intent',
      label: '问题覆盖',
      status: `部分 ${answered}/${coverage.length}`,
      tone: needs ? 'warn' : 'partial',
    };
  }
  return { id: 'intent', label: '问题覆盖', status: '未评估', tone: 'unknown' };
}

function planLayer(metadata: RecordValue | null | undefined, checks: Check[]): TrustLayer {
  const brief = (metadata?.analysis_brief || {}) as RecordValue;
  const hasPlan = !!brief.calculation_plan && typeof brief.calculation_plan === 'object';
  if (hasPlan) return { id: 'plan', label: '计算计划', status: '已声明', tone: 'ok' };
  if (checks.some((check) => check.id === 'calculation_contract')) {
    return { id: 'plan', label: '计算计划', status: '已声明', tone: 'ok' };
  }
  return { id: 'plan', label: '计算计划', status: '无计划', tone: 'unknown' };
}

function calculationLayer(checks: Check[], sources: RecordValue[], hasNumericResult: boolean): { layer: TrustLayer; unverifiedWarning?: string } {
  const groups = sources.length ? sources.map(source => list(source.checks) as Check[]) : [checks];
  const contracts = [...checks, ...groups.flat()].filter(check => check.id === 'calculation_contract');
  if (contracts.some(check => check.status === 'failed')) {
    return { layer: { id: 'calculation', label: '结果核对', status: '未通过', tone: 'fail' },
      unverifiedWarning: '本轮存在计算核对失败；已保留结果和失败依据，不能当作已核验结论使用。' };
  }
  if (contracts.some(check => check.status === 'warning')) {
    return { layer: { id: 'calculation', label: '结果核对', status: '需关注', tone: 'warn' } };
  }
  if (contracts.length && contracts.every(check => check.status === 'passed') && groups.every(group =>
    group.some(check => check.id === 'calculation_contract' && check.status === 'passed'))) {
    return { layer: { id: 'calculation', label: '结果核对', status: '已核对（符合计划）', tone: 'ok' } };
  }
  if (groups.length && groups.every(group => group.some(check => check.id === 'compiled_execution' && check.status === 'passed'))) {
    return {
      layer: { id: 'calculation', label: '结果核对', status: '按固定计划执行', tone: 'partial' },
      unverifiedWarning: '已按固定计算计划执行，未独立数值复算；计划是否完整理解原问题、业务定义与结论仍需分别核验。',
    };
  }
  if (hasNumericResult) {
    return {
      layer: { id: 'calculation', label: '结果核对', status: '未独立核对', tone: 'warn' },
      unverifiedWarning: '计算未独立核对：本轮至少一项结果缺少完整通过的计划核对回执；其他步骤通过不能代替本项。业务口径与结论需分别核验。',
    };
  }
  return { layer: { id: 'calculation', label: '结果核对', status: '未评估', tone: 'unknown' } };
}

function businessLayer(metadata: RecordValue | null | undefined, report: RecordValue | null | undefined, sources: RecordValue[]): TrustLayer {
  const ids = currentMetricIds(metadata, report);
  const dependencies = currentMetricDependencies(metadata, report).filter(item => ids.includes(String(item.metric_id)));
  if (dependencies.some(item => item.status !== 'ready')) {
    return { id: 'business', label: '业务口径', status: '需补资料或确认', tone: 'warn' };
  }
  const definitionChecks = sources.flatMap(source => list(source.checks)).filter(check => ['metric_definition','business_definition'].includes(String(check.id)));
  if (definitionChecks.some(check => check.status === 'failed')) {
    return { id: 'business', label: '业务口径', status: '未通过', tone: 'fail' };
  }
  const fixed = sources.length > 0 && sources.every(source => list(source.checks).some(check => check.id === 'business_definition' && check.status === 'passed' && Object.keys((check.definition_versions || {}) as RecordValue).length > 0));
  if(fixed)return {id:'business',label:'业务口径',status:'按已确认定义执行 · 未独立复算',tone:'partial'};
  const checked = ids.length > 0 && sources.length > 0 && sources.every(source => {
    const checks = list(source.checks).filter(check => check.id === 'metric_definition');
    const verified = checks.filter(check => check.status === 'passed').flatMap(check => Array.isArray(check.metric_ids) ? check.metric_ids.map(String) : []);
    const required = Array.isArray(source.metric_ids) && source.metric_ids.length ? source.metric_ids.map(String) : ids;
    return checks.length > 0 && checks.every(check => check.status === 'passed') && required.every(id => verified.includes(id));
  }) && ids.every(id => sources.some(source => list(source.checks).some(check =>
    check.id === 'metric_definition' && check.status === 'passed' && Array.isArray(check.metric_ids) && check.metric_ids.includes(id))));
  return { id: 'business', label: '业务口径', status: checked ? '已有对应指标核对回执' : '未核验', tone: checked ? 'ok' : 'unknown' };
}

/** Detect whether the reply exposes numeric/table/chart output a user might trust. */
export function hasNumericDelivery(metadata: RecordValue | null | undefined, report?: RecordValue | null): boolean {
  return deliverySources(metadata, report).length > 0 || list(report?.findings).length > 0;
}

export function buildDeliveryTrust(input: {
  checks?: Check[] | null;
  report?: RecordValue | null;
  metadata?: RecordValue | null;
  hasNumericResult?: boolean;
}): DeliveryTrust {
  const checks = [...list(input.metadata?.checks), ...(Array.isArray(input.checks) ? input.checks : [])] as Check[];
  const metadata = input.metadata ? { ...input.metadata, checks } : input.metadata;
  const sources = deliverySources(metadata, input.report);
  const numeric = input.hasNumericResult ?? hasNumericDelivery(input.metadata, input.report);
  const calc = calculationLayer(checks, sources, numeric);
  return {
    layers: [intentLayer(input.report), planLayer(input.metadata, [...checks, ...sources.flatMap(source => list(source.checks) as Check[])]), calc.layer, businessLayer(metadata, input.report, sources)],
    unverifiedWarning: calc.unverifiedWarning,
  };
}

export function shouldShowDeliveryTrust(input: {
  checks?: Check[] | null;
  report?: RecordValue | null;
  metadata?: RecordValue | null;
}): boolean {
  if(deliveryWorkflow(input.metadata||{},input.report).preparation.length)return true;
  if(deliveryOutcome(input.metadata||{},input.report))return true;
  if (input.report && (input.report as RecordValue).version === 1) return true;
  if (Array.isArray(input.checks) && input.checks.length > 0) return true;
  return hasNumericDelivery(input.metadata, input.report) || currentMetricIds(input.metadata, input.report).length > 0;
}
