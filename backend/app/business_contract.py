"""Confirmed business definitions -> deterministic relational plans."""
from __future__ import annotations

from datetime import date,timedelta
import re
from typing import Literal

from pydantic import BaseModel,ConfigDict,Field,model_validator

from app.calculation_contract import CalculationPlan,Predicate


class ContractModel(BaseModel):
    model_config=ConfigDict(extra='forbid')


class EntityDefinition(ContractModel):
    table:str=Field(min_length=1,max_length=100)
    keys:list[str]=Field(min_length=1,max_length=4)
    date_field:str=Field(default='',max_length=100)
    filters:list[Predicate]=Field(default_factory=list,max_length=12,description='Confirmed permanent entity rules, e.g. completed status. Put analysis periods in the request, never here.')


class MetricDefinition(ContractModel):
    model_config=ConfigDict(extra='forbid',json_schema_extra={
        'allOf':[{'if':{'properties':{'op':{'enum':['add','subtract','ratio']}}},
            'then':{'required':['numerator','denominator'],'properties':{
                'numerator':{'type':'string','minLength':1},'denominator':{'type':'string','minLength':1}}}}]})
    id:str=Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]{0,49}$')
    label:str=Field(min_length=1,max_length=100)
    unit:str=Field(min_length=1,max_length=30)
    op:Literal['sum','sum_product','count_rows','count_distinct','repeat_count','add','subtract','ratio']
    entity:EntityDefinition|None=None
    fact_table:str=Field(default='',max_length=100)
    entity_keys:list[str]=Field(default_factory=list,max_length=4,description='Join columns from entity; $month/$day mean the confirmed entity date bucket. Empty uses entity primary keys.')
    fact_keys:list[str]=Field(default_factory=list,max_length=4)
    field:str=Field(default='',max_length=100)
    left_field:str=Field(default='',max_length=100)
    right_field:str=Field(default='',max_length=100)
    event_field:str=Field(default='',max_length=100,description='For repeat_count: distinct event ID; field is the entity/person being counted.')
    filters:list[Predicate]=Field(default_factory=list,max_length=12)
    numerator:str=Field(default='',max_length=50,description='Derived metric ID, never a field or code.')
    denominator:str=Field(default='',max_length=50)
    multiplier:Literal[1,100]=Field(default=1,description='For ratios: 100 for percentage points, 1 for a fraction; fixed with the definition.')
    absence:Literal['unknown','zero']='unknown'
    unknowns:Literal['require_complete','known_subtotal']='require_complete'
    confirmation_quote:str=Field(min_length=1,max_length=1000)
    confirmation_message_id:int|None=None
    reason:str=Field(default='',max_length=500)

    @model_validator(mode='after')
    def complete_definition(self):
        if self.op in {'add','subtract','ratio'}:
            if not self.numerator or not self.denominator:raise ValueError('派生指标必须引用两个真实指标定义。')
        else:
            if not self.entity:raise ValueError('事实指标必须声明主体、唯一键与固定范围。')
            if self.op in {'sum','count_distinct','repeat_count'} and not self.field:raise ValueError('指标缺少实际字段。')
            if self.op=='sum_product' and not (self.left_field and self.right_field):raise ValueError('逐笔乘积缺少两个字段。')
            if self.op=='repeat_count' and not self.event_field:raise ValueError('复购/重复实体计数缺少事件唯一键。')
        return self


class AnalysisPeriod(ContractModel):
    start:str=Field(description='Inclusive ISO date.')
    end:str=Field(description='Exclusive ISO date: the next day/month start, not 23:59:59.')

    @model_validator(mode='after')
    def valid_period(self):
        if not 0<(date.fromisoformat(self.end)-date.fromisoformat(self.start)).days<=5000:
            raise ValueError('期间必须为不超过5000天的有效左闭右开区间。')
        return self


class MetricSelection(ContractModel):
    metric_id:str=Field(min_length=1,max_length=50)
    column:str=Field(min_length=1,max_length=100)


class MetricGrouping(ContractModel):
    field:str=Field(min_length=1,max_length=100,description='Actual entity column, or $day/$month based on its confirmed date field.')
    column:str=Field(min_length=1,max_length=100)


class MetricRequest(ContractModel):
    id:str=Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]{0,39}$')
    metrics:list[MetricSelection]=Field(min_length=1,max_length=8)
    period:AnalysisPeriod|None=None
    filters:list[Predicate]=Field(default_factory=list,max_length=12,description='Only this turn filters on entity fields; does not change any metric definition.')
    group_by:list[MetricGrouping]=Field(default_factory=list,max_length=4)
    complete_days:bool=False


class ContractError(ValueError):
    pass


def compile_requests(dataset,definitions,requests,question,confirmed=()):
    """Each fact sees one entity scope; fact tables never filter one another."""
    definitions={k:MetricDefinition.model_validate(v) for k,v in definitions.items()}
    available={f.table_name:set(f.columns) for f in dataset.files}
    stages=[];bindings={};used=set();windows=[]

    def closure(ids):
        order=[];visiting=set()
        def visit(identifier):
            if identifier in visiting:raise ContractError('指标定义存在循环依赖。')
            if identifier in order:return
            if identifier not in definitions:raise ContractError('缺少已确认指标定义：'+identifier)
            visiting.add(identifier);definition=definitions[identifier]
            if definition.op in {'add','subtract','ratio'}:
                visit(definition.numerator);visit(definition.denominator)
            visiting.remove(identifier);order.append(identifier)
        for identifier in ids:visit(identifier)
        if len(order)>24:raise ContractError('本轮指标依赖超过24项，请拆分任务。')
        return order

    def checked(table,column):
        if table not in available or column not in available[table]:raise ContractError('指标绑定的表或字段不存在：'+table+'.'+column)
        return table+'.'+column

    for index,raw_request in enumerate(requests):
        request=MetricRequest.model_validate(raw_request);prefix=f'_contract_{index}'
        if request.id in bindings:raise ContractError('交付请求ID重复。')
        order=closure([m.metric_id for m in request.metrics]);used.update(order)
        facts=[definitions[k] for k in order if definitions[k].op not in {'add','subtract','ratio'}]
        entities=[d.entity.model_dump() for d in facts]
        if not entities or any(e!=entities[0] for e in entities):raise ContractError('同一交付中的指标必须共享已确认的主体定义。')
        entity=facts[0].entity;table=entity.table
        for key in entity.keys:checked(table,key)
        if request.period and not entity.date_field:raise ContractError('主体尚未确认日期归属字段。')
        for predicate in [*entity.filters,*request.filters]:checked(table,predicate.field.removeprefix(table+'.'))
        if any(p.cast or isinstance(p.value,str) and re.match(r'^\d{4}-\d{2}',p.value) for p in entity.filters):
            raise ContractError('固定主体定义不能固化本轮日期范围，期间应放在分析请求中。')
        fields=set(entity.keys)
        for d in facts:
            keys=d.entity_keys or entity.keys
            fields.update(k for k in keys if k not in {'$day','$month'})
            if (d.fact_table or table)==table:
                fields.update(k for k in (d.field,d.left_field,d.right_field,d.event_field) if k)
        fields.update(g.field for g in request.group_by if g.field not in {'$day','$month'})
        if entity.date_field:fields.add(entity.date_field)
        select=[{'name':k,'op':'column','field':checked(table,k)} for k in sorted(fields)]
        symbolic={g.field for g in request.group_by}|{k for d in facts for k in d.entity_keys}
        for symbol in sorted(symbolic&{'$day','$month'}):
            if not entity.date_field:raise ContractError('日期分组没有已确认日期字段。')
            select.append({'name':'_bucket_'+symbol[1:],'op':'date_'+symbol[1:],'field':checked(table,entity.date_field)})
        def entity_field(name):return '_bucket_'+name[1:] if name.startswith('$') else name
        scope=prefix+'_entities';filters=[p.model_dump() for p in [*entity.filters,*request.filters]]
        if request.period:
            filters.extend({'field':checked(table,entity.date_field),'op':op,'value':value,'cast':'try_date'}
                for op,value in [('ge',request.period.start),('lt',request.period.end)])
        stages.append({'name':scope,'source':table,'select':select,'filters':filters})
        group_names=[g.column for g in request.group_by]
        if len(set(group_names))!=len(group_names):raise ContractError('分组输出列重复。')
        frame=prefix+'_groups'
        if request.complete_days:
            if not request.period or len(request.group_by)!=1 or request.group_by[0].field!='$day':
                raise ContractError('日期补齐当前只支持明确期间的单一逐日分组。')
            column=group_names[0]
            windows.append((request.period.start,(date.fromisoformat(request.period.end)-timedelta(days=1)).isoformat()))
            stages.append({'name':frame,'source':prefix+'_dates','calendar':{'column':column,'start':request.period.start,
                'end':(date.fromisoformat(request.period.end)-timedelta(days=1)).isoformat()},
                'group_by':[column],'select':[{'name':column,'op':'column','field':column},{'name':'_present','op':'count_rows'}]})
        else:
            stages.append({'name':frame,'source':scope,'group_by':[scope+'.'+entity_field(g.field) for g in request.group_by],
                'select':[*[{'name':g.column,'op':'column','field':scope+'.'+entity_field(g.field)} for g in request.group_by],{'name':'_present','op':'count_rows'}]})
        measures=[];joins=[]
        for number,d in enumerate(facts):
            fact=d.fact_table or table;keys=d.entity_keys or entity.keys;right=d.fact_keys or keys
            if len(keys)!=len(right):raise ContractError('事实与主体关联键数量不一致。')
            if keys!=entity.keys:
                raise ContractError('当前固定契约仅支持按主体唯一键关联事实；非唯一键或期间目标映射需先明确粒度，未执行。')
            for key in right:checked(fact,key)
            group_select=[{'name':g.column,'op':'column','field':scope+'.'+entity_field(g.field)} for g in request.group_by]
            group_fields=[scope+'.'+entity_field(g.field) for g in request.group_by]
            fact_filters=[p.model_dump() for p in d.filters]
            for p in d.filters:checked(fact,p.field.removeprefix(fact+'.'))
            # The entity scope is keyed, so independent fact aggregation never joins two fact tables.
            fact_join={'table':scope,'kind':'inner','left_keys':right,'right_keys':[entity_field(k) for k in keys]}
            name=prefix+f'_fact_{number}'
            expression={'name':'_value','op':d.op,'null_policy':d.unknowns,'empty_policy':d.absence,'policy_quote':d.confirmation_quote}
            if d.op in {'sum','count_distinct'}:expression['field']=checked(fact,d.field)
            if d.op=='sum_product':expression.update(numerator=checked(fact,d.left_field),denominator=checked(fact,d.right_field))
            if d.op=='repeat_count':
                events=name+'_events';person=checked(fact,d.field)
                stages.append({'name':events,'source':fact,'joins':[fact_join],'filters':fact_filters,
                    'group_by':[person,*group_fields],'select':[{'name':'_person','op':'column','field':person},*group_select,
                        {'name':'_events','op':'count_distinct','field':checked(fact,d.event_field),'null_policy':d.unknowns},
                        {'name':'_identity','op':'count_distinct','field':person,'null_policy':d.unknowns}]})
                valid=events+'_valid'
                stages.append({'name':valid,'source':events,'select':[
                    *[{'name':g,'op':'column','field':g} for g in group_names],
                    {'name':'_valid_events','op':'multiply','numerator':'_events','denominator':'_identity'}]})
                stages.append({'name':name,'source':valid,'group_by':group_names,
                    'select':[*[{'name':g,'op':'column','field':g} for g in group_names],
                        {'name':'_value','op':'count_if','field':'_valid_events','null_policy':d.unknowns,'condition':{'field':'_valid_events','op':'ge','value':2}},{'name':'_records','op':'count_rows'}]})
            else:
                stages.append({'name':name,'source':fact,'joins':[fact_join],'filters':fact_filters,'group_by':group_fields,
                    'select':[*group_select,expression,{'name':'_records','op':'count_rows'}]})
            alias='_metric_'+d.id
            if group_names:
                joins.append({'table':name,'kind':'left','left_keys':group_names,'right_keys':group_names,'null_safe':True})
                value={'name':alias,'op':'zero_if_unmatched','numerator':name+'._value','denominator':name+'._records','policy_quote':d.confirmation_quote} if d.absence=='zero' else {'name':alias,'op':'column','field':name+'._value'}
            else:
                joins.append({'table':name,'kind':'cross'});value={'name':alias,'op':'column','field':name+'._value'}
            measures.append(value)
        merged=prefix+'_merged'
        stages.append({'name':merged,'source':frame,'joins':joins,'select':[
            *[{'name':g,'op':'column','field':frame+'.'+g} for g in group_names],*measures]})
        calculated=prefix+'_calculated'
        expressions=[{'name':g,'op':'column','field':g} for g in group_names]+[{'name':'_metric_'+d.id,'op':'column','field':'_metric_'+d.id} for d in facts]
        for identifier in order:
            d=definitions[identifier]
            if d.op in {'add','subtract','ratio'}:expressions.append({'name':'_metric_'+d.id,'op':d.op,'numerator':'_metric_'+d.numerator,'denominator':'_metric_'+d.denominator,'multiplier':d.multiplier})
        stages.append({'name':calculated,'source':merged,'select':expressions})
        output=request.id;columns=[*group_names,*[m.column for m in request.metrics]]
        if len(set(columns))!=len(columns):raise ContractError('交付列名重复。')
        stages.append({'name':output,'source':calculated,'select':[
            *[{'name':g,'op':'column','field':g} for g in group_names],
            *[{'name':m.column,'op':'column','field':'_metric_'+m.metric_id} for m in request.metrics]],
            'order_by':[{'field':g} for g in group_names]})
        bindings[output]={'stage':output,'columns':columns,'metric_ids':[m.metric_id for m in request.metrics]}
    quotes=list(dict.fromkeys([question[:1000],*[definitions[k].confirmation_quote for k in sorted(used)]]))
    plan=CalculationPlan.model_validate({'source_quotes':quotes[:8],'stages':stages})
    return {'plan':plan.model_dump(),'bindings':bindings,'definition_ids':sorted(used),'windows':windows}
