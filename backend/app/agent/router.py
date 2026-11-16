"""Router node: classifies the question into one of four execution paths.

Uses Gemini with structured output (a Pydantic schema) so we always get a
valid route + reason without parsing free-form text.
"""
from __future__ import annotations

import logging
import json
import re
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, field_validator, model_validator, ValidationError
from langchain_core.exceptions import OutputParserException

from app.agent.state import AgentState
from app.llm import get_structured_llm
from app.execution import invoke_model
from app.schemas import ClarificationQuestion, AnalysisBrief
from app.answer_quality import analyst_guidance, ANALYST_INTENT_RULES

logger = logging.getLogger(__name__)


class RouteDecision(BaseModel):
    """Structured output from the router LLM call."""

    route: Literal["sql", "viz", "python", "chat", "clarify", "refuse", "cleaning", "investigate", "workflow"] = Field(
        ..., description="Which execution path to take."
    )
    reason: str = Field(
        ...,
        description=(
            "One short sentence justifying the route choice. "
            "For 'clarify', this is the question to ask the user. "
            "For 'refuse', this is the explanation."
        ),
    )
    show_data_preview: bool = Field(default=False,description='True only when the user requests an introduction to imported tables, field missingness or actual sample rows; attach an immutable source preview.')
    table_descriptions: dict[str,str] = Field(default_factory=dict,description='Only for a data overview: exact imported table name to one short description of business purpose and row grain. No counts, missingness guesses or business-state claims.')
    clarification: ClarificationQuestion | None = Field(default=None,description='For material intent gaps: collect ALL unresolved business decisions in decisions, matching each exact intent_gap question; give reasons, supported suggestions and impacts in this same call. Never ask again for confirmed inputs or invent a preferred choice without evidence.')
    cleaning_intent: Literal['preview','apply'] = Field(default='preview',description='apply only for an explicit user instruction to execute cleaning, including polite requests such as 那你可以直接帮我清洗吗. Inspection, advice, hypothetical execution and requests not to execute use preview.')
    python_chart: bool = Field(default=False,description='For the python route only: true when the user also requests a chart of the computed result table.')
    analysis_brief: AnalysisBrief = Field(description='Required on EVERY route, including chat/clarify: record EVERY requested part, its answer type, catalog metric IDs (profit stays profit), and actual user-confirmed scope sources. The execution and delivery graph consume this contract.')

    @classmethod
    def model_json_schema(cls, **kwargs):
        schema=super().model_json_schema(**kwargs)
        definitions=schema.get('$defs',{})
        definitions['AnalysisBrief']['properties'].pop('calculation_plan',None)
        definitions['AnalysisBrief'].setdefault('required',[]).extend(['metric_definitions','metric_requests'])
        needed=set()
        def visit(value):
            if isinstance(value,dict):
                reference=value.get('$ref','').removeprefix('#/$defs/')
                if reference in definitions and reference not in needed:
                    needed.add(reference);visit(definitions[reference])
                for key,child in value.items():
                    if key!='$defs':visit(child)
            elif isinstance(value,list):
                for child in value:visit(child)
        visit(schema)
        schema['$defs']={key:value for key,value in definitions.items() if key in needed}
        return schema

    @model_validator(mode="before")
    @classmethod
    def unwrap_duplicate_object(cls, value):
        if isinstance(value, dict) and set(value) == {"route"}:
            inner = value["route"]
            if isinstance(inner, dict) and {"route", "reason"}<=set(inner)<=set(cls.model_fields):
                return inner
        return value

    @field_validator("route", mode="before")
    @classmethod
    def unwrap_field_label(cls, value):
        if isinstance(value, str):
            # Repair a redundant field label; Literal still validates the route itself.
            match = re.fullmatch(r"route:\s*([\"'])([a-z]+)\1", value.strip())
            return match.group(2) if match else value
        return value


ROUTER_SYSTEM_PROMPT = """You are a routing classifier for a data analysis agent.

Resolve overlap by the DATA GRAIN, not the number of requested presentation forms.
One result table with several metrics at the same grain, its chart, and a conclusion
is ONE analytical task: choose viz, or python with python_chart when Python is required.
Never choose workflow merely because the user requests query + table + chart + explanation.
Period comparisons of amount, distinct orders and per-order amount at month grain
belong to viz when a chart is requested. Respect an explicit single-query/single-table scope.
Workflow is for actual product operations or genuinely independent results/grains;
report formatting alone does not require an extra workflow or a separate query.

User-confirmed dates, units, currency and definitions remain confirmed. Do not
ask for them again. Column names alone do not establish whether a field includes
freight, tax, fees or refunds. Clarification options should name the actual fields
and ask the user to choose; never attach an unconfirmed business interpretation.
Treat inactive metric templates as examples, not definitions of this user's data.
Offer only choices needed for the actual ambiguity. Two supported choices are
enough; do not invent an arbitrary difference/formula merely to fill a third slot.

Given a user question and a dataset schema, decide which path to take:
- "workflow" — call product tools for analysis boards, cleaning undo, relationship
               checks, Superset advanced charts (pivot table, treemap, sunburst, sankey),
               metric references, reading/exporting saved results, or a sequence
               such as clean -> analyze -> chart -> board. Also use this for multiple
               independent analytical questions that cannot be answered by one query.
               A table overview plus missingness impacts remains chat with preview.
               Pure business change diagnosis can still use investigate.
- "cleaning" — inspect uploaded data quality, recommend or revise preprocessing,
               clean/standardize/deduplicate/fill data, or approve a cleaning proposal.
               Set cleaning_intent=apply when the user explicitly requests execution.
               Otherwise diagnose and preview only. Never ask again for optional additions.
- "sql"      — answerable with a single SQL query (aggregations, filters, ranking, group by)
- "viz"      — user explicitly asks for a chart/plot/graph/visualization, OR the answer is much
               clearer as a visualization (time series, distributions, comparisons across many groups)
- "investigate" — diagnose business changes, find likely arithmetic drivers, or combine
               totals, order counts, average order amount and dimensional breakdowns.
               Examples: "为什么商品金额下降，帮我查查", "调查订单量和客单价的影响".
               This gathers evidence; it does not prove causation. Use when several
               purposeful queries are needed, including a chart if requested.
- "python"   — requires algorithms outside SQL such as statistical hypothesis tests,
               clustering, or scipy/sklearn routines; complexity alone is not a reason to use Python
               Also honor an explicit request to use Python. A Python calculation, its result
               table, chart and summary at the same grain are ONE python task, not workflow.
               Set python_chart=true if its computed table also needs a chart.
- "chat"     — general questions, summaries, explanations, suggestions, or anything that doesn't
               require running code against the data. Examples: "what columns do I have?",
               "summarize my data", "what can I ask?", "explain MRP", greetings, thank you, etc.
- "clarify"  — question is genuinely under-specified (vague metric, no time frame, ambiguous "best")
               and a reasonable analyst would need to ask before answering
- "refuse"   — question requires unsupported future forecasts, proof of causation,
               or PII surfacing. Historical statistical modeling with available scipy/sklearn
               uses python with honest evaluation; do not refuse all model fitting.

Rules:
- Separate requested methods and scope declarations from execution constraints.
  A request to explain hypotheses, assumptions, outlier rules or limitations is
  explanation/definition with an explicit subject, never invented result columns.
   A user declaration that observations are synthetic uses answer_type=scope and
   scope_key matching scope_sources, with the exact USER quotation in scope_sources.
   This acknowledges a declared scope; code cannot independently establish provenance.
   Prohibitions on cleaning, filtering or plotting remain constraint requirements.
   For an explanation of why statistical results alone do not prove causation,
   set method_topic=causal_boundary; other method explanations leave it empty.
   If the user explicitly requests numerical rounding, quote that USER instruction
   in scope_sources with key=precision; otherwise preserve full result precision.
  Editing/exporting a chart means its supported appearance and complete result
  exports; do not expand this to editing original data rows unless requested.
- Requests to check dirty data, recommend cleaning, change a prior cleaning plan,
  or start preprocessing use cleaning, not SQL/Python. A simple table/column overview
  still uses chat; a business aggregation with missing-value conditions uses SQL.
- A compound request must cover EVERY part. Never select a single SQL path for
  several unrelated result grains or silently drop cleanup, charts, boards or exports.
- System credentials, storage migration, arbitrary filesystem and external delivery
  are not chat tools. Ask or explain the actual limitation rather than claiming execution.
- For an introduction to uploaded tables, columns, missing-value profiles or
  sample rows, set show_data_preview=true. Existing schema missing counts are
  imported WHOLE-TABLE profiles; chat can explain them without a business SQL
  query. Keep this false for greetings, method proposals, ordinary business
  aggregates and unrelated chat. Filtered missing counts require a fresh query.
  For chat overviews, populate table_descriptions with one concise Chinese
  business-purpose/row-grain description per imported table. Do not include
  counts, missingness causes, filtered findings or inferred event statuses.
- DEFAULT to "sql" when the question is clear and answerable with aggregation/filter SQL.
- A natural-language "why" about an observed business change is not automatically
  a causal-proof request: choose investigate for contributions and evidence gathering.
  Explicit requests to prove an intervention caused an outcome remain unsupported
  without a valid identification design. Do not merely refuse all diagnostic work.
- Use the versioned metric catalog as reference. Clarify ambiguous sales/GMV/net
  amounts only when the question and prior confirmed context do not resolve them.
  Check only dependencies of the requested metric, not every catalog entry.
- Refund/net-receipt requests require actual settled refund amounts. If absent,
  ask for the real refund ledger and timing basis. Never propose order final
  statuses, cancellations, payment/item gaps or assumed zero as refund proxies.
- Schema examples are samples, not exhaustive row/value lists. When the needed
  columns already exist, use SQL to check whether a requested record or target
  exists. Do not ask the user to look up data the tools can query, or infer a
  missing record from its absence in schema examples. Clarify only genuinely
  missing inputs or undecided definitions, with evidence for any missing claim.
- Date differences, inter-order intervals, window functions and rolling aggregates
  are supported by DuckDB SQL; prefer sql for these rather than unnecessary Python.
- Cohort retention, observation-window eligibility, conditional rates and first-event
  anchoring can use joins, CTEs and window functions. Use sql, or viz when a supported
  chart is requested, even if several CTEs are needed. Python execution needs a separate
  isolated runtime; never select it merely to reshape a SQL result into a heatmap.
- Use "viz" when the user says "show me", "plot", "chart", "graph", "visualize", "trend over time",
  except an explicit Python/statistical task uses python with python_chart=true.
- Use "clarify" sparingly. Don't ask trivial clarifications. Only when a single best
  interpretation is genuinely unclear (e.g., "how are sales doing?" — too vague).
- Use "refuse" for requests like "what will revenue be next quarter?" or "prove that
  our campaign caused churn to drop" without the needed causal design. For an
  ordinary investigation of churn changes, use investigate and retain uncertainty.

FOLLOW-UPS: If the prompt includes "PREVIOUS QUERIES IN THIS SESSION", the
current question may be a follow-up that refines an earlier one (e.g.,
"now break that down by region", "only the last 6 months", "make it a bar
chart instead"). In that case:
  - If the user asked to switch to a chart, route to "viz".
  - Otherwise route to the same kind of path the previous query used.
  - Do NOT clarify just because pronouns refer to prior context; resolving
    "that" against the most recent query is your job.

Return your decision via the structured schema. The `reason` field:
- For sql/viz: ONE short sentence explaining why this path fits.
- For clarify: the actual clarifying question to put in front of the user.
  Ask a concrete question and identify each unresolved input needed for the requested
  metric by its actual column name. Schema 'missing: N' counts are measured NULLs,
  not guesses. For revenue, inspect quantity and unit_price; for profit inspect
  costs too. If deduplication or negative-value rules are undecided, ask about them
  as well. Do not claim a field is missing when no evidence shows it, or silently
  drop incomplete rows, fill values, or change the user's metric.
  If the user needs a complete result, explicitly request the actual missing input
  values. Excluding affected rows or offering a subtotal is only an optional
  alternative, not a substitute for obtaining the requested complete result.
- For refuse: the actual explanation to put in front of the user.

LANGUAGE: Always write the `reason` field in Simplified Chinese.
Keep route identifiers, column names, and original data values unchanged.
"""


ROUTER_SYSTEM_PROMPT += '\n数值、比较、表、图必须填写真实指标或结果列。方法定义、检验前提、因果限制属于非数值说明，用subject明确对象，不要编造结果列。不清洗、不筛行、不绘图等执行限制用constraint单列，不能拿它替代实际统计目标；保留在analysis_brief传给执行器。'
ROUTER_SYSTEM_PROMPT += '''
For new analysis_brief output use parts: [{requirement: exact requested part, criterion: {...}}].
Do not also populate legacy requirements/acceptance arrays. The program assigns indices.
Split distinct constraints into separate parts. constraint_kind: output_shape for explicit result row/column requirements;
preserve_source only for preserving ORIGINAL tables (not proof that observations were never filtered); precision for unrounded values.
Use custom fixed metric definitions for imported data, not catalog names with a different business meaning.
Do not bypass profit/cost dependencies by relabeling revenue or net cash. Current confirmations use message_id=null.
Keep JSON compact: omit unused defaults. A requested explanation is not a numerical output column.
'''


from app.calculation_contract import CALCULATION_GUIDANCE


ROUTER_SYSTEM_PROMPT += """
固定业务计算优先：已确认的指标定义与本轮请求必须分开。对可表达的业务求和、逐笔乘积、计数、重复事件、差额和比率，使用metric_definitions与metric_requests，不再提供自由calculation_plan。
已有定义直接引用metric_id，追问只改period、filters、group_by与交付；不得重写公式、状态或无记录规则。新的定义先按真实字段提出，confirmation_quote必须是实际用户原话；没有明确确认时程序会展示建议供分析师决定。
只填result_columns不能执行。sql/viz的业务数值问题必须填写非空metric_requests，首次必须同时填写metric_definitions；它们不是可省略的附加说明。chat/clarify/cleaning无计算时才可为空。
最小结构示例（仅示意，实际使用当前表字段和真实原话）：
{"metric_definitions":[{"id":"duration","label":"时长","unit":"小时","op":"sum","entity":{"table":"tasks","keys":["task_id"],"date_field":"occurred_on"},"field":"hours","confirmation_quote":"用户实际定义原话"}],"metric_requests":[{"id":"total","metrics":[{"metric_id":"duration","column":"时长"}],"period":{"start":"2025-01-01","end":"2025-02-01"}}],"parts":[{"requirement":"一月时长","criterion":{"answer_type":"value","metric_request_id":"total","request_quote":"用户实际问题原话"}}]}
主体entity声明唯一键、日期归属和永久状态范围。各事实独立关联主体，不能把无退款主体排除。无记录按0和金额未知保留NULL是不同规则，按用户确认填写absence与unknowns。
事实关联当前仅支持主体唯一键，非唯一映射、首购归属与尚不能表达的方法明确说明限制，不伪称已核验。period是左闭右开的ISO日期，只用用户明确的绝对月份/日期范围，不猜年份。
派生指标add/subtract/ratio必须填numerator和denominator，值为依赖指标ID（不是字段），不能省略或改填left_field/right_field。例如{"id":"net","label":"净额","unit":"元","op":"subtract","numerator":"gross","denominator":"deduction","confirmation_quote":"用户实际确认原话"}。
每项数值/图表交付的criterion.metric_request_id引用实际请求ID，程序填写列和阶段。不同粒度、不同期间分别给请求，可分别输出当前/基准期间；解释与方法边界单列，不虚构数值列。不要把纯清洗、撤销或系统操作转成指标查询。
"""


def _get_router_llm():
    return get_structured_llm(RouteDecision, json_mode=True)


def router_node(state: AgentState) -> dict[str, Any]:
    if state.get("planned_sql"):
        return {"route": "sql", "route_reason": state.get("route_reason", "Investigation selected SQL")}

    question = state["question"]
    schema = state["schema"]
    session_context = state.get("session_context", "")

    from app.agent.workflow import saved_undo_plan
    undo=saved_undo_plan(state)
    if undo:
        from app.analysis_pipeline import prepare_request
        prepared=prepare_request(state,{'goal':question,'requirements':[question],'acceptance':[
            {'requirement_index':0,'answer_type':'constraint','subject':'撤销当前清洗并标明历史结果需重算','request_quote':question}]})
        prepared['analysis_brief']['workflow_plan']=undo.model_dump()
        return {**prepared,'route':'workflow','route_reason':'按明确撤销指令执行当前已保存操作，不重新生成计算计划。'}

    llm = _get_router_llm()
    user_msg = f"Schema:\n{schema}\n\n"
    from app.metrics import METRICS
    user_msg+='Reference metric catalog (not proof of mapped data):\n'+json.dumps([
        {key:metric[key] for key in ('id','label','profile','required_roles','definition','calculation') if key in metric} for metric in METRICS.values()],ensure_ascii=False)+'\n\n'
    if session_context:
        user_msg += f"{session_context}\n\n"
    if state.get('session_id'):
        from app.session import get_session_store
        from app.answer_acceptance import prepare_brief
        messages=get_session_store()._backend.get_messages(state['session_id'])
        confirmed=prepare_brief(state,{'requirements':[question],'acceptance':[]},messages=messages)['scope_sources']
        if confirmed:
            user_msg+='Exact USER context and confirmed rules (current question overrides older rules; do not ask these again):\n'+json.dumps(
                [{'key':s['key'],'text':s['text'],'message_id':s.get('message_id')} for s in confirmed],ensure_ascii=False)+'\n\n'
    from app.analysis_contract_store import load_registry
    registry=load_registry(state['session_id']) if state.get('session_id') else {'active':{},'versions':{}}
    if registry['active']:
        user_msg+='Confirmed fixed metric definitions (reuse IDs; do not redefine on period/group followups):\n'+json.dumps(
            {k:registry['versions'][v] for k,v in registry['active'].items()},ensure_ascii=False)+'\n'
    pending=next(((m.get('metadata') or {}).get('analysis_brief',{}).get('pending_metric_definitions')
        for m in reversed(messages if state.get('session_id') else []) if m['role']=='assistant'),None)
    if pending:user_msg+='Pending definitions, NOT confirmed: '+json.dumps(pending,ensure_ascii=False)+'\n'
    user_msg += f"Current question: {question}"

    try:
        decision: RouteDecision = invoke_model(llm, [
            SystemMessage(content=ROUTER_SYSTEM_PROMPT+ANALYST_INTENT_RULES+analyst_guidance(question,session_context)
                +'\n只输出一个有效JSON对象，遵循 JSON Schema：'+json.dumps(RouteDecision.model_json_schema(), ensure_ascii=False)),
            HumanMessage(content=user_msg),
        ])
    except (ValidationError,OutputParserException) as exc:
        detail=''
        if isinstance(exc,ValidationError):
            detail=json.dumps(exc.errors(include_input=False,include_context=False),ensure_ascii=False)[:1500]
        elif exc.llm_output:
            try:RouteDecision.model_validate_json(exc.llm_output)
            except ValidationError as invalid:detail=json.dumps(invalid.errors(include_input=False,include_context=False),ensure_ascii=False)[:1500]
        logger.warning('Analysis planning validation failed: %s',detail or type(exc).__name__)
        message='模型未提供完整、可核对的分析目标，本次未执行查询或更改数据。已有结果保留，不会自动补发模型请求。'
        return {'route':'refuse','route_reason':message,'error':message,
            'checks':[{'id':'analysis_contract','status':'failed','detail':message+(' 校验原因：'+detail if detail else '')}]}

    logger.info("Route: %s — %s", decision.route, decision.reason)
    from app.analysis_pipeline import prepare_request
    brief=decision.analysis_brief.model_dump()
    prepared=prepare_request({**state,'route':decision.route},brief)
    bound=prepared['analysis_brief']
    if bound.get('contract_error'):
        message='固定业务计算尚不能执行：'+bound['contract_error']
        return {**prepared,'route':'refuse','route_reason':message,'error':message,
            'checks':[{'id':'business_definition','status':'failed','detail':message}]}
    if bound.get('contract_question'):
        return {**prepared,'route':'clarify','route_reason':bound['contract_question']['question'],
            'clarification':bound['contract_question']}
    if bound.get('business_contract'):
        decision.route='viz' if any(c.get('answer_type')=='chart' for c in bound['acceptance']) else 'sql'
        from app.calculation_contract import request_output_columns
        try:request_output_columns({**state,**prepared})
        except ValueError as exc:
            message='固定业务计算准备未通过，未改写已确认规则或重复请求模型：'+str(exc)
            return {**prepared,'route':'refuse','route_reason':message,'error':message,
                'checks':[{'id':'business_definition','status':'failed','detail':message}]}
    if prepared.get('analysis_scope'):
        from app.execution import save_partial_result
        save_partial_result({**state,**prepared})
    from app.answer_acceptance import calculation_brief
    if decision.route=='investigate' and brief.get('calculation_plan'):
        decision.route='sql'
    if not bound.get('business_contract') and decision.route in {'sql', 'viz', 'python'} and brief.get('calculation_plan') and calculation_brief(prepared['analysis_brief'])['acceptance']:
        from app.calculation_contract import CalculationPlan, request_output_columns
        try:
            request_output_columns({**state, **prepared})
        except ValueError as exc:
            attempted = {'plan': brief['calculation_plan'], 'error': str(exc)}
            prepared['analysis_brief']['calculation_plan_attempts'] = [attempted]
            try:
                corrected = invoke_model(get_structured_llm(CalculationPlan), [
                    SystemMessage(content='Correct only the declared read-only calculation plan. Use the actual schema and exact USER quotations. '
                        'Preserve every requested filter, join grain, output column, NULL policy and precision. '
                        'A stage reads only its source and explicit joins. Do not invent fields, SQL, values or user confirmations. '
                        'When scalar aggregation and arithmetic are mixed, SUM the raw operands into aliases first; do not add GROUP BY to hide an invalid total. '+CALCULATION_GUIDANCE),
                    HumanMessage(content=user_msg+'\nUnchanged requirements and confirmed scope:\n'+json.dumps(
                        {k: prepared['analysis_brief'][k] for k in ('requirements', 'acceptance', 'scope_sources')}, ensure_ascii=False)
                        +'\nFailed plan and validation error:\n'+json.dumps(attempted, ensure_ascii=False)),
                ])
                prepared['analysis_brief']['calculation_plan'] = corrected.model_dump()
                prepared.update(prepare_request({**state,**prepared}))
                request_output_columns({**state, **prepared})
            except (ValueError, OutputParserException) as repair_error:
                detail = '模型返回的计算计划格式无效。' if isinstance(repair_error, (ValidationError, OutputParserException)) else str(repair_error)
                message = '计算计划经一次纠错仍未通过核对，未执行查询：'+detail
                return {**prepared, 'route': 'refuse', 'route_reason': message, 'error': message,
                    'checks': [{'id': 'analysis_contract', 'status': 'failed', 'detail': message}]}
    if decision.route in {'sql','viz'}:
        from app.agent.workflow import calculation_workflow
        split=calculation_workflow(prepared['analysis_brief'])
        if split:
            prepared['analysis_brief']['workflow_plan']=split.model_dump()
            return {**prepared,'route':'workflow','route_reason':'本轮要求对应不同汇总粒度，分别执行并核对，保留每项结果。'}
    dependencies=prepared['analysis_brief']['metric_dependencies']
    ready={d['metric_id'] for d in dependencies if d['status']=='ready'}
    calculation=any(s.get('answer_type') in {'value','comparison','explanation','table','chart'} and s.get('metric_ids') and set(s['metric_ids'])<=ready for s in prepared['analysis_brief']['acceptance'])
    route='sql' if decision.route=='chat' and calculation and dependencies and all(d['status']=='ready' for d in dependencies) else decision.route
    if decision.route=='chat' and any(s.get('custom_metric_reason') for s in prepared['analysis_brief']['acceptance']):
        route='sql'
    blocked={d['metric_id'] for d in dependencies if d['status']=='needs_input'}
    numerical=[s for s in prepared['analysis_brief']['acceptance'] if s.get('answer_type') in {'value','comparison','explanation','table','chart'}]
    if numerical and all(s.get('intent_gap') or s.get('metric_ids') and set(s['metric_ids'])<=blocked for s in numerical):
        route='chat'
        from app.answer_acceptance import dependency_answer
        prepared['dependency_response']=dependency_answer(prepared['analysis_brief'])
    gaps=prepared['analysis_brief']['intent_review']['gaps']
    clarification=decision.clarification
    if gaps:
        from app.clarification import group_decisions
        clarification=ClarificationQuestion.model_validate(group_decisions(prepared['analysis_brief'],clarification))
        if all(spec.get('intent_gap') for spec in prepared['analysis_brief']['acceptance']):
            route='clarify'
    if route in {'sql','viz','python'}:
        from app.answer_acceptance import dependency_requirements
        covered=dependency_requirements(prepared['analysis_brief'])
        prepared['analysis_brief']['active_requirement_ids']=[s['id'] for s in prepared['analysis_brief']['acceptance'] if s['id'] not in covered]
    return {"route": route, "route_reason": decision.reason,"show_data_preview":route=='chat' and decision.show_data_preview,
            'table_descriptions':decision.table_descriptions,'cleaning_intent':decision.cleaning_intent,
            'python_chart':decision.route=='python' and decision.python_chart,
            'clarification':clarification.model_dump() if clarification else None,
            **prepared}


def decide_after_router(state: AgentState) -> str:
    """Conditional edge: where to go after the router based on the chosen route."""
    return state["route"]
