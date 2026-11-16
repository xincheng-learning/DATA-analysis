"""Approve user-sourced definitions once, then bind requests to immutable versions."""
import hashlib
import json
import re
from datetime import date,timedelta

from app.analysis_contract_store import load_registry,save_definition
from app.business_contract import MetricDefinition,MetricRequest,ContractError,compile_requests
from app.tools.join_validation import _quote


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def explicit_confirmation(text):
    return bool(re.search(r'(?:^|[\n。；;]|业务背景[：:])\s*(?:本任务|我|现在)?(?:已确认[：:]|确认[：:]|确认采用|确认使用|确认以下)',text))


def period_windows(brief,question):
    """Normalize only actual absolute user dates/months, not model dates."""
    sources=brief.get('scope_sources',[])
    selected=next((s['text'] for s in sources if s['key']=='selected_period'),'')
    context='\n'.join([question,*[s['text'] for s in sources]])
    text=selected or question.rsplit('当前问题：',1)[-1]
    if not selected:
        window=re.search(r'(?:报告窗口|报表范围|日期窗口)[^\n。]*',context)
        if window:text=window[0]
    windows=[]
    for a,b in re.findall(r'(\d{4}-\d{2}-\d{2})\s*(?:至|到|~|—|through|to)\s*(\d{4}-\d{2}-\d{2})',text,re.I):
        end=date.fromisoformat(b)
        if not re.search(r'不含|之前|左闭右开|exclusive',text,re.I):end+=timedelta(days=1)
        windows.append((a,end.isoformat()))
    years=set(re.findall(r'(?<!\d)(\d{4})(?:年|[-/])',context))
    months=[]
    for year,month in re.findall(r'(?:(\d{4})年)?(\d{1,2})月',text):
        year=year or (next(iter(years)) if len(years)==1 else '')
        if year:months.append((int(year),int(month)))
    months.extend((int(y),int(m)) for y,m in re.findall(r'(\d{4})-(\d{2})(?!-\d{2})',text))
    for year,month in set(months):
        start=date(year,month,1);end=date(year+int(month==12),1 if month==12 else month+1,1)
        windows.append((start.isoformat(),end.isoformat()))
    if len(windows)>1:
        ordered=sorted(set(windows))
        if all(a[1]==b[0] for a,b in zip(ordered,ordered[1:])):windows.append((ordered[0][0],ordered[-1][1]))
    return list(dict.fromkeys(windows))


def validate_period(period,brief,question):
    if period is None:
        if any(s['key']=='selected_period' for s in brief.get('scope_sources',[])):
            raise ContractError('本轮期间尚不能确定，未按全量执行。')
        return
    if (period.start,period.end) not in period_windows(brief,question):
        raise ContractError('计算期间没有匹配已确认的绝对月份或日期窗口；未猜测期间或沿用错误月份。')


def bind_contracts(state,brief,dataset,messages):
    question=state.get('user_request') or state.get('question','')
    current=question.rsplit('当前问题：',1)[-1].strip().rstrip('。！!')
    previous=next(((m.get('metadata') or {}).get('analysis_brief',{}) for m in reversed(messages)
        if m['role']=='assistant'),{})
    drafts=previous.get('pending_metric_definitions') or []
    approved_pending=bool(drafts and current=='确认采用以上全部指标定义')
    if approved_pending:
        from app.tools.data_workbench import workbench_snapshot
        if previous.get('pending_contract_data_version')!=workbench_snapshot(dataset):
            raise ContractError('数据版本已变化，请重新查看定义建议后确认。')
        brief={**brief,'metric_definitions':[{**d,'confirmation_quote':current,'confirmation_message_id':None} for d in drafts],
            'metric_requests':previous['metric_requests'],'acceptance':previous['acceptance'],
            'requirements':previous['requirements'],'scope_sources':previous.get('scope_sources',[])}
    raw_requests=brief.get('metric_requests') or []
    if not raw_requests:return brief
    if state.get('internal_analysis') and brief.get('business_contract'):
        return brief
    if dataset is None:raise ContractError('没有可用于指标定义的当前数据。')
    question=state.get('user_request') or state.get('question','')
    users=[m for m in messages if m['role']=='user' and (m.get('metadata') or {}).get('type')!='upload']
    bodies=[{'id':None,'content':question},*reversed(users)]
    registry=load_registry(state['session_id'])
    active={identifier:registry['versions'][version] for identifier,version in registry['active'].items()}
    candidates=[MetricDefinition.model_validate(d) for d in brief.get('metric_definitions',[])]
    if len({d.id for d in candidates})!=len(candidates):raise ContractError('指标定义ID重复。')
    pending=[];approved=[]
    for definition in candidates:
        content=definition.model_dump();old=active.get(definition.id)
        semantic=lambda value:MetricDefinition.model_validate(value).model_dump(exclude={'confirmation_quote','confirmation_message_id','reason'})
        if old and semantic(old)==semantic(content):continue
        source=next((m for m in bodies if definition.confirmation_quote in m['content'] and
            (definition.confirmation_message_id is None or definition.confirmation_message_id==m['id'])),None)
        if source is None:raise ContractError('指标确认引用不属于真实用户原话：'+definition.label)
        revised=bool(re.search(r'(?:改|调整|重新定义).{0,12}(?:口径|公式|定义|计算方式)|(?:口径|公式|定义|计算方式).{0,12}(?:改|调整|定义为)',question))
        allowed=explicit_confirmation(source['content'])
        if old:allowed=allowed and source['id'] is None and (revised or approved_pending)
        if not allowed:
            pending.append(content);continue
        validate_entity_keys(dataset,[content])
        content['confirmation_message_id']=source['id'];approved.append(content)
    if pending:
        from app.tools.data_workbench import workbench_snapshot
        suggestions=[]
        for d in pending:
            expression=(d.get('left_field','')+' × '+d.get('right_field','')) if d['op']=='sum_product' else (d.get('field') or d.get('numerator','')+' / '+d.get('denominator',''))
            if d['op'] in {'add','subtract','ratio'}:
                expression=d['numerator']+{'add':' + ','subtract':' − ','ratio':' / '}[d['op']]+d['denominator']
            entity=d.get('entity') or {}
            suggestions.append(d['label']+'（'+d['unit']+'）：'+d['op']+'('+expression+')；主体='+entity.get('table','依赖指标主体')+
                '；日期='+entity.get('date_field','继承')+'；固定筛选='+json.dumps([*entity.get('filters',[]),*d.get('filters',[])],ensure_ascii=False)+
                '；无记录='+('0' if d['absence']=='zero' else '未知')+'；已有未知='+('总量未知' if d['unknowns']=='require_complete' else '仅已知小计')+
                '；建议理由：'+(d.get('reason') or '按当前问题与字段提出，仍需你判断字段含义及规则是否正确。'))
        text='请核对以下指标定义：\n'+'\n'.join(suggestions)
        if len(text)>1000:raise ContractError('指标定义建议超过单次确认卡容量，请拆分确认，未静默省略规则。')
        return {**brief,'pending_metric_definitions':pending,'pending_contract_data_version':workbench_snapshot(dataset),'contract_question':{
            'question':text,'why':'公式、主体范围或缺失策略会改变结果。确认后会复用定义，不在追问时重新决定。',
            'options':['确认采用以上全部指标定义','需要调整定义']}}
    combined={**active,**{d['id']:d for d in approved}}
    requests=[MetricRequest.model_validate(r) for r in raw_requests]
    for request in requests:
        if request.period is None and any(s['key']=='selected_period' for s in brief.get('scope_sources',[])):
            windows=period_windows(brief,question)
            if len(windows)!=1:raise ContractError('本轮期间存在多个可能范围，请明确选择，未猜测或按全量执行。')
            from app.business_contract import AnalysisPeriod
            request.period=AnalysisPeriod(start=windows[0][0],end=windows[0][1])
        validate_period(request.period,brief,question)
    sources=list(dict.fromkeys(d['confirmation_quote'] for d in combined.values()))
    compiled=compile_requests(dataset,combined,[r.model_dump() for r in requests],question,sources)
    criteria=[]
    for spec in brief.get('acceptance',[]):
        ref=spec.get('metric_request_id')
        if ref:
            if ref not in compiled['bindings']:raise ContractError('交付要求没有对应的指标计算请求。')
            binding=compiled['bindings'][ref]
            spec={**spec,'result_stage':binding['stage'],'result_columns':binding['columns']}
        criteria.append(spec)
    unused={d['id'] for d in approved}-set(compiled['definition_ids'])
    if unused:raise ContractError('未使用的候选指标不能随本轮计算静默确认：'+','.join(sorted(unused)))
    from app.calculation_contract import CalculationPlan,compile_plan
    query,_=compile_plan(dataset,CalculationPlan.model_validate(compiled['plan']),question,sources,authorized_windows=compiled['windows'])
    dataset.get_connection().execute('DESCRIBE '+query).fetchall()
    for definition in approved:save_definition(state['session_id'],definition)
    registry=load_registry(state['session_id'])
    versions={key:registry['active'][key] for key in compiled['definition_ids']}
    contract={'version':1,'definition_versions':versions,'requests':[r.model_dump() for r in requests],
        'windows':compiled['windows'],'sources':sources,'bindings':compiled['bindings'],
        'stage_hashes':[digest(stage) for stage in compiled['plan']['stages']]}
    return {**brief,'acceptance':criteria,'calculation_plan':compiled['plan'],'business_contract':contract,
        'business_contract_hash':digest(contract)}


def contract_compile_inputs(brief,session_id=None,dataset=None):
    """Private preparation metadata, never accepted from the model schema."""
    contract=brief.get('business_contract')
    if not contract:return [],[]
    if digest(contract)!=brief.get('business_contract_hash'):
        raise ContractError('固定指标请求已变化，请重新准备，未继续执行。')
    stages=(brief.get('calculation_plan') or {}).get('stages',[])
    if not stages or [digest(stage) for stage in stages]!=contract['stage_hashes'][:len(stages)]:
        raise ContractError('计算步骤偏离已确认指标的程序生成结果，未继续执行。')
    if session_id:
        registry=load_registry(session_id)
        active=registry['active']
        if any(active.get(k)!=v for k,v in contract['definition_versions'].items()):
            raise ContractError('已确认指标定义版本已变化，旧结果需重算，未继续执行旧依据。')
        if dataset:validate_entity_keys(dataset,[registry['versions'][v] for v in contract['definition_versions'].values()])
    return contract['sources'],contract['windows']


def validate_entity_keys(dataset,definitions):
    from app.tools.data_workbench import workbench_snapshot
    schema={f.table_name:set(f.columns) for f in dataset.files}
    for content in definitions:
        definition=MetricDefinition.model_validate(content)
        if not definition.entity:continue
        entity=definition.entity
        fields=set(entity.keys)|{p.field.removeprefix(entity.table+'.') for p in entity.filters}
        if entity.date_field:fields.add(entity.date_field)
        if not fields<=schema.get(entity.table,set()):raise ContractError('主体定义引用了当前数据不存在的字段。')
        check=(workbench_snapshot(dataset),entity.table,tuple(entity.keys))
        cache=getattr(dataset,'_contract_key_checks',set())
        if check in cache:continue
        keys=','.join(_quote(k) for k in entity.keys)
        nulls=' OR '.join(_quote(k)+' IS NULL' for k in entity.keys)
        original=dataset.last_query_checks
        try:
            invalid=dataset.execute('SELECT '+keys+' FROM '+_quote(entity.table)+' GROUP BY '+keys+' HAVING COUNT(*)>1 OR '+nulls+' LIMIT 1',as_frame=True)
        finally:dataset.last_query_checks=original
        if len(invalid):raise ContractError('主体唯一键存在重复或未知值，请先检查主体粒度，未执行该定义。')
        dataset._contract_key_checks={*cache,check}
