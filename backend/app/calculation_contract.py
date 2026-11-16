"""Execute a restricted declared calculation independently of candidate code.

Agreement certifies the declared plan, not interpretation of arbitrary user intent.
No gold IDs, answers, candidate SQL or executable Python are used to build it.
"""
import hashlib
import json
import math
import re
from datetime import date
from numbers import Integral, Real
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from app.tools.join_validation import _quote


class CalculationMismatch(ValueError):
    """Candidate output disagrees with an executable declared plan."""
    check_id = 'calculation_contract'
    retryable = True


class Predicate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    field: str = Field(min_length=1, max_length=200)
    op: Literal['eq', 'ne', 'gt', 'ge', 'lt', 'le', 'is_null', 'not_null']
    value: str | int | float | bool | None = None
    cast: Literal['', 'date', 'timestamp', 'try_date', 'try_timestamp'] = ''


class Expression(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    op: Literal['column', 'sum', 'sum_if', 'sum_product', 'multiply', 'mean', 'count', 'count_rows', 'count_distinct', 'std_samp', 'std_pop', 'min', 'max', 'ratio', 'add', 'subtract', 'count_if', 'zero_if_unmatched','case','date_month','date_day','count_distinct_over']
    field: str = Field(default='', max_length=200)
    numerator: str = Field(default='', max_length=200,description='Ratio numerator or left operand for add/subtract/multiply/sum_product; sum_product uses a row-level field, never code.')
    denominator: str = Field(default='', max_length=200,description='Ratio denominator or right operand for add/subtract/multiply/sum_product; sum_product uses a row-level field, never code.')
    multiplier: float = Field(default=1.0, allow_inf_nan=False)
    condition: Predicate | None = None
    value: str | int | float | bool | None = None
    else_value: str | int | float | bool | None = None
    cases: list['CaseWhen'] = Field(default_factory=list,max_length=8)
    null_policy: Literal['unspecified','known_subtotal','require_complete'] = Field(default='unspecified',description='For numeric aggregates: known_subtotal is not a complete total; require_complete returns NULL if any input is unknown.')
    empty_policy: Literal['unknown','zero'] = Field(default='unknown',description='Zero only for genuinely empty aggregate input, with a user policy_quote; never fill existing unknown values.')
    policy_quote: str = Field(default='',max_length=1000,description='Exact USER rule authorizing zero for empty/unmatched records. zero_if_unmatched: numerator=right measure, denominator=right LEFT JOIN key. Matched NULL stays NULL.')


class CaseWhen(BaseModel):
    model_config = ConfigDict(extra='forbid')
    condition: Predicate
    value: str | int | float | bool | None = None


Expression.model_rebuild()


class Join(BaseModel):
    model_config = ConfigDict(extra='forbid')
    table: str = Field(min_length=1, max_length=100)
    kind: Literal['inner', 'left', 'cross'] = 'inner'
    allow_fanout: bool = Field(default=False,description='True only for explicitly requested expansion. LEFT JOIN enriching an already grouped source otherwise requires at most one right match per left key.')
    null_safe: bool = False
    left_keys: list[str] = Field(default_factory=list, max_length=8)
    right_keys: list[str] = Field(default_factory=list, max_length=8, description='Empty keys only for joining an earlier scalar aggregate stage; never a raw table.')


class Order(BaseModel):
    field: str = Field(min_length=1, max_length=200)
    descending: bool = False


class Calendar(BaseModel):
    model_config = ConfigDict(extra='forbid')
    column: str = Field(min_length=1,max_length=100)
    start: str = Field(description='Inclusive ISO date explicitly requested by the user, not a guessed data extent.')
    end: str = Field(description='Inclusive ISO date explicitly requested by the user.')


class Stage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]*$', max_length=50)
    source: str = Field(min_length=1, max_length=100)
    select: list[Expression] = Field(min_length=1, max_length=32)
    filters: list[Predicate] = Field(default_factory=list, max_length=16)
    joins: list[Join] = Field(default_factory=list, max_length=5)
    group_by: list[str] = Field(default_factory=list, max_length=12)
    order_by: list[Order] = Field(default_factory=list, max_length=8)
    partition_by: list[str] = Field(default_factory=list,max_length=8,description='Entity keys for count_distinct_over and first_by; not a grouped entity/channel pair.')
    first_by: list[Order] = Field(default_factory=list,max_length=8,description='Keep one earliest row per partition after filters. Include a deterministic tie breaker. Window counts still cover all rows before this selection.')
    calendar: Calendar | None = Field(default=None,description='Generate all requested daily dates as source, then left join measured aggregates. Never fill unknown amounts with zero.')
    entity_key: str = Field(default='',max_length=200,description='For count_rows/count_if of entities: source entity ID must be unique within each output group; entity+channel rows are not overall entities.')
    scope_exemption_quote: str = Field(default='',max_length=1000,description='Exact USER rule explicitly requiring a different scope, e.g. an all-history baseline. Never exempt an accidentally unfiltered measure.')


class CalculationPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_quotes: list[str] = Field(default_factory=list, max_length=8, description='Program attaches actual user request provenance; optional exact USER quotations, never schema or assistant confirmations.')
    stages: list[Stage] = Field(min_length=1, max_length=24, description='Read-only relational calculation. Prefer at most six model-authored stages; deterministic metric contracts may produce more. No SQL/code or expected values.')
    row_count: int | None = Field(default=None, ge=0, le=5000, description='Only if the user explicitly requires an exact result row count.')
    ordered_rows: bool = False
    ordered_columns: bool = False


CALCULATION_GUIDANCE = '''
All SQL/viz analyze steps need their own calculation_plan. Reuse declared stage prefixes and requirement bindings; do not discard the plan and ask another model to guess SQL.
Related raw-fact aggregates must share the declared entity period: reuse a filtered entity stage and join it by key for every related measure. A date filter on one measure does not scope another. An intentionally different all-history baseline needs scope_exemption_quote from the USER.
Bind derived results to the stage actually SELECTing every requested column, not an earlier intermediate stage.
For comparisons/pivots use sum_if(field,condition), not two unconditional SUMs. Use date_month/date_day for buckets; preserve the confirmed null_policy.
Text judgements use op=case with cases=[{condition,value},...] and else_value. Missing, zero and positive targets need separate branches; zero is not nonzero.
For a complete daily window use calendar={column,start,end} with exact inclusive ISO USER dates, then LEFT JOIN measurements. Missing amounts stay NULL. Daily tables, graphs AND matching exports must include every requested day, not just recorded dates.
For every LEFT JOIN apply the confirmed absence policy: no event means zero ONLY when USER says so; use zero_if_unmatched with the actual right join key and policy_quote. A bare nullable projection omits this rule. Matched unknown amounts remain NULL.
LEFT JOIN enrichment of grouped results preserves the left grain by default. Filter reference tables to the confirmed period and use all business keys. allow_fanout is only for user-requested expansion, not a way to suppress lookup errors.
Count entities after entity-grain aggregation, setting entity_key. First-event assignment uses partition_by=[entity], first_by=[date,event ID], count_distinct_over(field=event ID), then groups one row per entity by its assigned channel.
Keep JSON compact; omit unused defaults. Never invent inputs, confirmations, units or literal results.
'''


def prepare_daily_window(plan,criteria,text):
    """Make an explicitly requested daily window an execution step, not a model reminder."""
    window=re.search(r'(?:报告窗口|报表范围|日期窗口|report window)\s*(?:为|[:：])?\s*(\d{4}-\d{2}-\d{2})\s*(?:至|到|~|—|through|to)\s*(\d{4}-\d{2}-\d{2})',text,re.I)
    if not window or not re.search(r'每日|每天|逐日|日报|daily|each day',text,re.I):return plan
    names={s['name'] for s in plan['stages']};deliveries=set()
    for spec in criteria:
        columns=set(spec.get('result_columns',[]))
        matches=[s['name'] for s in plan['stages'] if columns and columns<={e['name'] for e in s['select']}]
        if spec.get('result_stage') in matches:deliveries.add(spec['result_stage'])
        elif matches:deliveries.add(matches[-1])
    stages=[];complete=set()
    for i,stage in enumerate(plan['stages']):
        if stage.get('calendar') or stage['source'] in complete or any(j['table'] in complete for j in stage.get('joins',[])):
            complete.add(stage['name']);stages.append(stage);continue
        dates=[e['name'] for e in stage['select'] if e['op']=='date_day' or e['op']=='column' and re.search(r'(?:date|day|日期|日期列)$',e.get('field',''),re.I)]
        if stage['name'] not in deliveries or len(dates)!=1 or len(stage.get('group_by',[]))>1 or stage.get('first_by'):
            stages.append(stage);continue
        recorded=f'_observed_{i}';calendar=f'_calendar_{i}';source=f'_dates_{i}';column=dates[0]
        if {recorded,calendar,source}&names or len(plan['stages'])+2>6:
            stages.append(stage);continue
        select=[]
        for e in stage['select']:
            if e['name']==column:select.append({'name':column,'op':'column','field':calendar+'.'+column})
            elif e['op'] in {'count','count_rows','count_if','count_distinct'}:
                select.append({'name':e['name'],'op':'zero_if_unmatched','numerator':recorded+'.'+e['name'],'denominator':recorded+'.'+column,'policy_quote':window.group(0)})
            else:select.append({'name':e['name'],'op':'column','field':recorded+'.'+e['name']})
        stages.extend([{**stage,'name':recorded},
            {'name':calendar,'source':source,'calendar':{'column':column,'start':window[1],'end':window[2]},'select':[{'name':column,'op':'column','field':source+'.'+column}]},
            {'name':stage['name'],'source':calendar,'joins':[{'table':recorded,'kind':'left','left_keys':[column],'right_keys':[column]}],
             'select':select,'order_by':[{'field':column}]}])
        complete.add(stage['name'])
    if len(stages)>6:return plan
    return {**plan,'stages':stages}


def source_fingerprint(dataset):
    return [(file.table_name, hashlib.sha256(file.parquet_path.read_bytes()).hexdigest()) for file in dataset.files]


def request_output_columns(state):
    from app.answer_acceptance import calculation_brief
    brief = calculation_brief(state.get('analysis_brief', {}))
    if not brief.get('calculation_plan'):
        return None
    from app.agent.workflow import calculation_workflow
    split=calculation_workflow(brief)
    if split:
        for step in split.steps:
            child={**brief,'calculation_plan':step.arguments['calculation_plan'],
                'active_requirement_ids':[brief['acceptance'][i]['id'] for i in step.covers]}
            child.pop('workflow_plan',None)
            request_output_columns({**state,'analysis_brief':child})
        return None
    from app.tools.dataset_manager import get_dataset_manager
    dataset = get_dataset_manager().get(state.get('session_id', ''))
    if dataset is None:
        raise ValueError('声明计算计划没有可核对的当前会话数据。')
    requested = list(dict.fromkeys(name for s in brief.get('acceptance', []) for name in s.get('result_columns', [])))
    plan = CalculationPlan.model_validate(brief['calculation_plan'])
    from app.temporal_scope import verify_temporal_scope
    verify_temporal_scope(brief, state.get('user_request') or state.get('question', ''))
    from app.business_contract_binding import contract_compile_inputs
    contract_sources,windows=contract_compile_inputs(brief,state.get('session_id'),dataset)
    query, columns = compile_plan(dataset, plan,
        state.get('user_request') or state.get('question', ''), [s[key] for s in [*brief.get('scope_sources', []),*brief.get('continuation_sources',[])] for key in ('text','source_question') if s.get(key)]+contract_sources, requested,authorized_windows=windows)
    from app.formula_contract import verify_user_formulas
    verify_user_formulas(dataset, brief, plan, state.get('user_request') or state.get('question', ''))
    import duckdb
    with dataset.query_lock:
        original_checks = dataset.last_query_checks
        try:
            result = dataset.execute(query, as_frame=True)
            if plan.row_count is not None and len(result) != plan.row_count:
                raise ValueError('声明计算计划的实际行数不符合请求的输出约束。')
        except duckdb.Error as exc:
            raise ValueError('声明计算计划无法执行：'+str(exc)) from exc
        finally:
            dataset.last_query_checks = original_checks
    return columns


def _verify_shared_scope(dataset,plan,question,confirmed):
    columns={f.table_name:set(f.columns) for f in dataset.files};scopes={};by_stage={}
    for stage in plan.stages:
        signatures={}
        for p in stage.filters:
            if not isinstance(p.value,str) or not re.match(r'^\d{4}-\d{2}',p.value) or not re.search(r'date|time|month|日期|时间',p.field,re.I):continue
            owner,_,name=p.field.rpartition('.')
            owners=[owner] if owner else [t for t in [stage.source,*[j.table for j in stage.joins]] if p.field in columns.get(t,set())]
            if len(owners)==1 and owners[0] in columns:
                signatures.setdefault(owners[0],[]).append((name or p.field,p.op,p.value))
        by_stage[stage.name]={owner:tuple(sorted(parts)) for owner,parts in signatures.items()}
        for owner,signature in by_stage[stage.name].items():scopes.setdefault(owner,set()).add(signature)
    shared={owner:next(iter(parts)) for owner,parts in scopes.items() if len(parts)==1};covered={}
    for stage in plan.stages:
        active=set(covered.get(stage.source,set()))
        for join in stage.joins:
            if join.left_keys and join.kind!='cross':active.update(covered.get(join.table,set()))
        active.update(owner for owner,signature in by_stage[stage.name].items() if shared.get(owner)==signature)
        covered[stage.name]=active
        if stage.scope_exemption_quote:
            if not any(stage.scope_exemption_quote in body for body in [question,*confirmed]):raise ValueError('不同范围的计算需要真实用户依据。')
            continue
        if stage.source not in columns or not any(e.op in {'sum','sum_if','sum_product','mean','count','count_rows','count_distinct','count_if'} for e in stage.select):continue
        for owner in shared:
            keys={c for c in columns[stage.source]&columns[owner] if re.search(r'(?:_id|^id|^key)$',c,re.I)}
            if owner not in active and (stage.source==owner or keys):
                raise ValueError('共同期间范围未在相关度量中体现：'+stage.source+' 的汇总没有沿用 '+owner+' 的已声明期间；请关联已筛选的实体阶段，或用真实用户原话声明不同范围。')


def compile_plan(dataset, plan, question, confirmed=(), requested=(), *, authorized_windows=()):
    if not plan.source_quotes or not all(quote.strip() and any(quote in text for text in [question, *confirmed]) for quote in plan.source_quotes):
        raise ValueError('声明计算计划缺少可核对的用户原话，未认证该口径。')
    _verify_shared_scope(dataset,plan,question,confirmed)
    available = {file.table_name: set(file.columns) for file in dataset.files}
    scalar_sources = set();grouped_sources=set()
    clauses = []
    final_columns = []
    for stage_index, stage in enumerate(plan.stages):
        calendar_source=None;calendar_alias=None
        if stage.calendar:
            calendar=stage.calendar
            start,end=date.fromisoformat(calendar.start),date.fromisoformat(calendar.end)
            if not 0<=(end-start).days<5000:
                raise ValueError('日期序列必须为不超过5000天的有效范围。')
            if not any(tuple(window)==(calendar.start,calendar.end) for window in authorized_windows) and not all(any(value in text for text in [question,*confirmed]) for value in (calendar.start,calendar.end)):
                raise ValueError('日期补齐的起止日期必须有用户原话依据，不能猜测范围。')
            if stage.source in available:
                if stage.filters or stage.group_by or stage.joins or calendar.column not in available[stage.source] or any(e.op!='column' for e in stage.select):
                    raise ValueError('已有数据源的日期补齐须用于投影阶段；请先完成筛选与汇总，再补齐日期。')
                calendar_alias=f'__date_frame_{stage_index}'
                if calendar_alias==stage.source:calendar_alias+='_dates'
            alias=calendar_alias or stage.source
            calendar_source="(SELECT strftime(day,'%Y-%m-%d') AS "+_quote(calendar.column)+" FROM generate_series(DATE '"+calendar.start+"',DATE '"+calendar.end+"',INTERVAL '1 day') AS dates(day)) AS "+_quote(alias)
            if calendar_alias:
                calendar_source+=' LEFT JOIN '+_quote(stage.source)+' ON '+_quote(calendar_alias)+'.'+_quote(calendar.column)+'='+_quote(stage.source)+'.'+_quote(calendar.column)
        if stage.name in available or stage.source not in available and not stage.calendar:
            raise ValueError('声明计算计划引用了不存在的表/阶段或覆盖已有名称。')
        scope = {stage.source:available.get(stage.source,{stage.calendar.column} if stage.calendar else set())}
        for join in stage.joins:
            if join.table not in available or join.table in scope or len(join.left_keys) != len(join.right_keys):
                raise ValueError('声明计算计划的关联表或键无效。')
            if not join.left_keys and join.table not in scalar_sources:
                raise ValueError('无关联键时右侧必须是前面已声明的单行汇总阶段，不能无条件关联原始表。')
            if join.kind == 'cross' and join.left_keys:
                raise ValueError('单行汇总cross关联不能同时指定关联键。')
            scope[join.table] = available[join.table]

        left_sources={j.table for j in stage.joins if j.kind=='left'}
        for p in stage.filters:
            owner,_,column=p.field.rpartition('.')
            owners=[owner] if owner else [t for t,names in scope.items() if p.field in names]
            if len(owners)==1 and owners[0] in left_sources and p.op!='is_null':
                raise ValueError('LEFT JOIN右侧筛选会排除无匹配记录的主体，导致总额漏算；请先在右侧阶段筛选状态或期间，再LEFT JOIN。若只要匹配主体，应明确使用INNER JOIN。')

        def field(name):
            if calendar_alias and name in {stage.calendar.column,stage.source+'.'+stage.calendar.column}:
                return _quote(calendar_alias)+'.'+_quote(stage.calendar.column)
            if '.' in name:
                table, column = name.split('.', 1)
                if table not in scope or column not in scope[table]:
                    raise ValueError('声明计算计划引用了未知字段：'+name)
            else:
                matches = [table for table, names in scope.items() if name in names]
                if len(matches) != 1:
                    raise ValueError('声明计算计划字段不存在或不唯一：'+name)
                table, column = matches[0], name
            return _quote(table)+'.'+_quote(column)

        def predicate(item):
            left = field(item.field)
            if item.cast:
                cast_type=item.cast.removeprefix('try_').upper()
                left = f'{"TRY_CAST" if item.cast.startswith("try_") else "CAST"}({left} AS {cast_type})'
            if item.op in {'is_null', 'not_null'}:
                return left+(' IS NULL' if item.op == 'is_null' else ' IS NOT NULL')
            value = item.value
            if value is None or isinstance(value, float) and not math.isfinite(value):
                raise ValueError('声明计算计划比较值必须明确且有限；空值请使用is_null/not_null。')
            literal = "'"+value.replace("'", "''")+"'" if isinstance(value, str) else str(value).upper()
            if item.cast:
                literal = f'CAST({literal} AS {item.cast.removeprefix("try_").upper()})'
            operator = {'eq': '=', 'ne': '<>', 'gt': '>', 'ge': '>=', 'lt': '<', 'le': '<='}[item.op]
            return f'{left} {operator} {literal}'

        expressions = []
        aliases = {}
        final_columns = [item.name for item in stage.select]
        if len(set(final_columns)) != len(final_columns):
            raise ValueError('声明计算计划输出列名重复。')
        ordered = [item for item in stage.select if item.op not in {'ratio','add','subtract','multiply'}]
        pending = [item for item in stage.select if item.op in {'ratio','add','subtract','multiply'}]
        defined = {item.name for item in ordered}
        while pending:
            ready = [item for item in pending if {name for name in (item.numerator, item.denominator)
                if name in final_columns} <= defined]
            if not ready:
                raise ValueError('声明计算计划的运算字段有循环或未解析依赖。')
            ordered.extend(ready)
            defined.update(item.name for item in ready)
            pending = [item for item in pending if item not in ready]
        for item in ordered:
            if item.op=='zero_if_unmatched' or item.empty_policy=='zero':
                if not item.policy_quote.strip() or not any(item.policy_quote in text for text in [question,*confirmed]):
                    raise ValueError('缺记录按零处理需要可追溯的用户业务依据；未将未知值补零。')
            if item.op == 'column':
                value = field(item.field)
            elif item.op in {'date_month','date_day'}:
                value="strftime(TRY_CAST("+field(item.field)+" AS DATE),'"+('%Y-%m' if item.op=='date_month' else '%Y-%m-%d')+"')"
            elif item.op=='case':
                branches=item.cases or ([CaseWhen(condition=item.condition,value=item.value)] if item.condition else [])
                if not branches:raise ValueError('条件说明缺少判断条件。')
                def literal(value):
                    if value is None:return 'NULL'
                    if isinstance(value,float) and not math.isfinite(value):raise ValueError('条件值必须有限。')
                    return "'"+value.replace("'","''")+"'" if isinstance(value,str) else str(value).upper()
                value='CASE '+''.join('WHEN '+predicate(branch.condition)+' THEN '+literal(branch.value)+' ' for branch in branches)+'ELSE '+literal(item.else_value)+' END'
            elif item.op=='count_distinct_over':
                if not stage.partition_by or stage.group_by:raise ValueError('实体事件窗口需要partition_by，不能同时GROUP BY。')
                value='COUNT(DISTINCT '+field(item.field)+') OVER (PARTITION BY '+','.join(field(k) for k in stage.partition_by)+')'
            elif item.op == 'count_rows':
                value = 'COUNT(*)'
            elif item.op == 'count_if':
                if item.condition is None:
                    raise ValueError('声明计算计划count_if缺少条件。')
                value = 'COUNT(*) FILTER (WHERE '+predicate(item.condition)+')'
            elif item.op == 'sum_product':
                value = 'SUM(('+field(item.numerator)+') * ('+field(item.denominator)+'))'
            elif item.op=='sum_if':
                if item.condition is None:raise ValueError('条件合计缺少明确的筛选条件。')
                value='SUM('+field(item.field)+') FILTER (WHERE '+predicate(item.condition)+')'
            elif item.op == 'zero_if_unmatched':
                owner,_,key=item.denominator.partition('.')
                presence=any(s.name==owner and any(e.name==key and e.op=='count_rows' for e in s.select) for s in plan.stages[:stage_index])
                join=next((j for j in stage.joins if j.table==owner and j.kind=='left' and (presence or key in [k.split('.')[-1] for k in j.right_keys])),None)
                if join is None or not item.numerator.startswith(owner+'.'):
                    raise ValueError('仅能依据LEFT JOIN右侧实际关联键判断未匹配；不能依据金额NULL补零。')
                value='CASE WHEN '+field(item.denominator)+' IS NULL THEN 0 ELSE '+field(item.numerator)+' END'
            elif item.op in {'ratio','add','subtract','multiply'}:
                adjusted={field(e.numerator):e.name for e in stage.select if e.op=='zero_if_unmatched'}
                for operand in (item.numerator,item.denominator):
                    if operand not in aliases and field(operand) in adjusted:
                        raise ValueError('派生指标绕过本阶段已声明的缺记录处理；请引用处理后的输出列 '+adjusted[field(operand)]+'，或明确拆分不同口径。')
                numerator = aliases[item.numerator] if item.numerator in aliases else field(item.numerator)
                denominator = aliases[item.denominator] if item.denominator in aliases else field(item.denominator)
                value = (f'({item.multiplier} * ({numerator}) / NULLIF(({denominator}),0))' if item.op=='ratio' else
                         f'(({numerator}) '+{'add':'+','subtract':'-','multiply':'*'}[item.op]+f' ({denominator}))')
            else:
                functions = {'sum': 'SUM', 'mean': 'AVG', 'count': 'COUNT', 'count_distinct': 'COUNT',
                             'std_samp': 'STDDEV_SAMP', 'std_pop': 'STDDEV_POP', 'min': 'MIN', 'max': 'MAX'}
                value = functions[item.op]+'('+('DISTINCT ' if item.op == 'count_distinct' else '')+field(item.field)+')'
            if item.op in {'sum','sum_if','sum_product','mean','min','max','std_samp','std_pop','count_distinct'} or item.op=='count_if' and item.field:
                inputs=[item.numerator,item.denominator] if item.op=='sum_product' else [item.field]
                if item.empty_policy=='zero' and any(join.kind=='left' and any(field(name).startswith(_quote(join.table)+'.') for name in inputs) for join in stage.joins):
                    raise ValueError('LEFT JOIN右侧无记录会保留空行，不能用COUNT(*)=0实现empty_policy=zero；请先独立汇总右表，再以zero_if_unmatched判断实际匹配键。')
                if item.null_policy=='require_complete':
                    observed='('+field(item.numerator)+')*('+field(item.denominator)+')' if item.op=='sum_product' else field(item.field)
                    subset=' FILTER (WHERE '+predicate(item.condition)+')' if item.op=='sum_if' else ''
                    value='CASE WHEN COUNT('+observed+')'+subset+'=COUNT(*)'+subset+' THEN '+value+' ELSE NULL END'
                if item.empty_policy=='zero':
                    subset=' FILTER (WHERE '+predicate(item.condition)+')' if item.op=='sum_if' else ''
                    value='CASE WHEN COUNT(*)'+subset+'=0 THEN 0 ELSE '+value+' END'
            if stage.entity_key and item.op in {'count_rows','count_if'}:
                value="CASE WHEN COUNT(*)<>COUNT(DISTINCT "+field(stage.entity_key)+") THEN error('实体粒度不唯一：先按实体汇总，再统计；不能累计跨分组的实体记录') ELSE "+value+' END'
            expressions.append(value+' AS '+_quote(item.name))
            aliases[item.name] = value
        expressions = [aliases[name]+' AS '+_quote(name) for name in final_columns]
        if stage_index == len(plan.stages)-1 and requested:
            if not set(requested) <= set(final_columns):
                raise ValueError('声明计算计划缺少请求的输出列：'+','.join(name for name in requested if name not in final_columns))
            # Declared intermediate measures can feed a ratio without becoming requested output columns.
            final_columns = [name for name in final_columns if name in requested]
            expressions = [aliases[name]+' AS '+_quote(name) for name in final_columns]
        query = 'SELECT '+','.join(expressions)+' FROM '+(calendar_source or _quote(stage.source))
        for join in stage.joins:
            preserve_grain=join.kind=='left' and stage.source in grouped_sources and join.left_keys and not join.allow_fanout
            join_source=_quote(join.table)
            if preserve_grain:
                keys=','.join(_quote(k.split('.')[-1]) for k in join.right_keys)
                if '__agent_join_matches' in available[join.table]:raise ValueError('关联校验保留列名发生冲突：__agent_join_matches')
                join_source='(SELECT *,COUNT(*) OVER (PARTITION BY '+keys+') AS __agent_join_matches FROM '+join_source+') AS '+_quote(join.table)
            query += ' '+join.kind.upper()+' JOIN '+join_source
            condition = ' AND '.join(
                field(left if '.' in left else stage.source+'.'+left)+(' IS NOT DISTINCT FROM ' if join.null_safe else '=')+
                field(right if '.' in right else join.table+'.'+right) for left, right in zip(join.left_keys, join.right_keys))
            if join.kind != 'cross':
                if preserve_grain:
                    condition+=' AND CASE WHEN '+_quote(join.table)+".__agent_join_matches<=1 THEN TRUE ELSE error('关联粒度不唯一：汇总结果关联到多条参考记录，请补全关联键或先筛选正确期间；需要展开时明确声明allow_fanout') END"
                query += ' ON '+(condition or 'TRUE')
        if stage.filters:
            query += ' WHERE '+' AND '.join(predicate(item) for item in stage.filters)
        if stage.group_by:
            query += ' GROUP BY '+','.join(aliases[name] if name in aliases else field(name) for name in stage.group_by)
        if stage.first_by:
            if not stage.partition_by or stage.group_by:raise ValueError('首笔归属需要实体partition_by，且必须在分组前执行。')
            query+=' QUALIFY ROW_NUMBER() OVER (PARTITION BY '+','.join(field(k) for k in stage.partition_by)+' ORDER BY '+','.join(field(o.field)+(' DESC' if o.descending else ' ASC')+' NULLS LAST' for o in stage.first_by)+')=1'
        if stage.order_by:
            query += ' ORDER BY '+','.join((_quote(item.field) if item.field in final_columns else field(item.field))+
                (' DESC' if item.descending else ' ASC') for item in stage.order_by)
        clauses.append(_quote(stage.name)+' AS ('+query+')')
        available[stage.name] = set(final_columns)
        aggregates = {'sum', 'sum_if', 'sum_product', 'mean', 'count', 'count_rows', 'count_distinct', 'count_if', 'std_samp', 'std_pop', 'min', 'max'}
        if not stage.group_by and (any(item.op in aggregates for item in stage.select)
                or stage.source in scalar_sources and all(join.table in scalar_sources for join in stage.joins)):
            scalar_sources.add(stage.name)
        if stage.group_by or stage.first_by or stage.name in scalar_sources or stage.source in grouped_sources and not any(j.allow_fanout for j in stage.joins):
            grouped_sources.add(stage.name)
    if plan.ordered_rows and not plan.stages[-1].order_by:
        raise ValueError('声明计算计划要求顺序但没有order_by。')
    # Keep ORDER BY in the final SELECT, including sort fields not projected to output.
    return ('WITH '+','.join(clauses[:-1])+' ' if len(clauses) > 1 else '')+query, final_columns


def verify_calculation(dataset, brief, question, columns, rows, row_count, *, sql=None, compiled=False):
    from app.answer_acceptance import calculation_brief
    brief = calculation_brief(brief)
    raw = brief.get('calculation_plan')
    if not raw:
        return []
    from app.temporal_scope import verify_temporal_scope
    try:
        verify_temporal_scope(brief, question, sql=sql, connection=dataset._con)
    except ValueError as exc:
        raise CalculationMismatch(str(exc)) from None
    if len(rows) != row_count:
        raise CalculationMismatch('声明计算计划必须核对完整结果，不能只使用预览行。')
    plan = CalculationPlan.model_validate(raw)
    requested = list(dict.fromkeys(name for s in brief.get('acceptance', []) for name in s.get('result_columns', [])))
    from app.business_contract_binding import contract_compile_inputs
    contract_sources,windows=contract_compile_inputs(brief,dataset.session_id,dataset)
    query, names = compile_plan(dataset, plan, question, [s[key] for s in [*brief.get('scope_sources', []),*brief.get('continuation_sources',[])] for key in ('text','source_question') if s.get(key)]+contract_sources, requested,authorized_windows=windows)
    from app.formula_contract import verify_user_formulas
    formula_checks = verify_user_formulas(dataset, brief, plan, question)
    if compiled:
        if len(columns)!=len(set(columns)) or set(columns)!=set(names) or plan.ordered_columns and columns!=names:
            raise CalculationMismatch('固定计划的实际输出列不一致。')
        if plan.row_count is not None and row_count!=plan.row_count:
            raise CalculationMismatch('固定计划的实际行数不符合输出要求。')
        from app.metric_contract import verify_metric_definition
        original_checks=dataset.last_query_checks
        try:metric_checks=verify_metric_definition(dataset,brief,plan,list(original_checks))
        finally:dataset.last_query_checks=original_checks
        refs=[s['id'] for s in brief.get('acceptance',[]) if s.get('result_columns') and set(s['result_columns'])<=set(names)]
        policies=[]
        labels={'known_subtotal':'仅汇报已知小计，不视为完整总量','require_complete':'存在未知输入时总量保留未知'}
        for stage in plan.stages:
            for item in stage.select:
                if item.null_policy in labels:policies.append(item.name+'：'+labels[item.null_policy])
                if item.op=='zero_if_unmatched' or item.empty_policy=='zero':policies.append(item.name+'：仅无记录时按0，匹配记录的未知值不补0；依据：'+item.policy_quote)
        semantics=[{'id':'data_semantics','status':'passed','detail':'；'.join(policies),'scope':'核对固定计划所执行的缺失处理规则及用户引用，不认证业务来源齐全。'}] if policies else []
        return [{'id':'compiled_execution','status':'passed','requirement_ids':refs,'columns':names,'row_count':row_count,
            'detail':'由固定声明计划编译SQL并实际执行；版本与代码已核对，不等于独立数值复算或完整业务语义认证。'},
            {'id':'calculation_contract','status':'not_checked','detail':'编译与执行依据同源，未将同源重算标为独立核验。'},*semantics,*formula_checks,*metric_checks,*([{'id':'business_definition','status':'passed','definition_versions':brief['business_contract']['definition_versions'],
                'detail':'按用户确认的指标定义版本执行；确认来源可追溯，不代表独立数值复算或解释正确。'}] if brief.get('business_contract') else [])]
    original_checks = dataset.last_query_checks
    try:
        expected = dataset.execute(query, as_frame=True)
        reference_checks = list(dataset.last_query_checks)
    finally:
        dataset.last_query_checks = original_checks
    expected_rows = [{k: None if pd.isna(v) else v for k, v in row.items()} for row in expected.to_dict('records')]
    if len(columns) != len(set(columns)) or set(columns) != set(names) or plan.ordered_columns and columns != names:
        raise CalculationMismatch('实际输出列与声明计算计划不一致；要求列为'+','.join(names)+'。')
    if len(expected_rows) != row_count or plan.row_count is not None and row_count != plan.row_count:
        raise CalculationMismatch('实际结果行数与声明计算计划不一致。')
    def values(data):
        records = [tuple(row[name] for name in names) for row in data]
        if not plan.ordered_rows:
            records.sort(key=lambda row: tuple((0, '') if value is None else (1, float(value)) if isinstance(value, Real) else (2, str(value)) for value in row))
        return records
    def equal(left, right):
        if left is None or right is None:
            return left is right
        if isinstance(left, bool) or isinstance(right, bool):
            return type(left) is type(right) and left == right
        if isinstance(left, Integral) or isinstance(right, Integral):
            return left == right
        if isinstance(left, Real) and isinstance(right, Real):
            return math.isfinite(float(left)) and math.isfinite(float(right)) and math.isclose(left, right, rel_tol=1e-10, abs_tol=0)
        return type(left) is type(right) and left == right
    if not all(all(equal(left, right) for left, right in zip(a, b)) for a, b in zip(values(rows), values(expected_rows))):
        raise CalculationMismatch('结果与声明计算计划的数值、NULL、重复数或顺序不一致；已停止交付该结果。')
    from app.metric_contract import verify_metric_definition
    try:
        metric_checks = verify_metric_definition(dataset, brief, plan, reference_checks)
    finally:
        dataset.last_query_checks = original_checks
    refs = [s['id'] for s in brief.get('acceptance', []) if s.get('result_columns') and set(s['result_columns']) <= set(names)]
    return [{'id': 'calculation_contract', 'status': 'passed', 'requirement_ids': refs, 'columns': names,
        'row_count': row_count, 'reference_sql': query,
        'plan_sha256': hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            'detail': '完整结果与声明计算计划一致，已核对筛选、聚合、分母、NULL、重复与顺序；计划的用户意图理解及统计适用性未独立认证。'}, *formula_checks, *metric_checks]


def constraint_checks(brief, columns, row_count, checks, source_unchanged):
    from app.answer_acceptance import calculation_brief
    brief = calculation_brief(brief)
    plan = brief.get('calculation_plan') or {}
    verified = any(c['id'] in {'calculation_contract','compiled_execution'} and c['status'] == 'passed' for c in checks)
    output = [e['name'] for e in plan.get('stages', [{}])[-1].get('select', [])]
    if source_unchanged is False:
        raise CalculationMismatch('原始数据文件在执行前后发生变化，未交付基于不同输入的结果。')
    receipts = [{'id': 'source_integrity', 'status': 'passed',
        'detail': '执行前后所有原始数据文件SHA-256一致；这是实际输入核对，不依赖模型是否记下不改原表的要求，也不证明计算没有取子集。'}] if source_unchanged is True else []
    for spec in brief.get('acceptance', []):
        if spec.get('answer_type') != 'constraint':
            continue
        kind = spec.get('constraint_kind')
        detail = ''
        if kind == 'preserve_source' and source_unchanged is True:
            detail = '执行前后各原始数据文件SHA-256一致；不证明计算过程没有取子集。'
        elif kind == 'output_shape' and verified and plan.get('row_count') == row_count and columns == output:
            detail = '完整结果的行数、列名与列顺序符合声明的输出约束。'
        elif kind == 'precision' and verified:
            detail = '原始数值与声明计算计划在严格相对容差内一致，非零小数没有被绝对容差吞成0。'
        if detail:
            receipts.append({'id': 'execution_constraint', 'status': 'passed', 'requirement_id': spec['id'], 'detail': detail})
    return receipts
