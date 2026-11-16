"""Compare a frozen declared analysis across isolated cleaning versions."""
import hashlib
import json
import re
from copy import deepcopy
from decimal import Decimal

import duckdb
import pandas as pd

from app.answer_acceptance import calculation_brief
from app.calculation_contract import CalculationPlan,compile_plan
from app.config import settings
from app.session import SQLiteBackend,get_session_store
from app.tools.dataset_manager import SessionDataset


def analysis_reference(dataset,table,current=None):
    if settings.app_mode!='local':return None
    backend=get_session_store()._backend
    if not isinstance(backend,SQLiteBackend):return None
    messages=backend.get_messages(dataset.session_id)
    users=[m['content'] for m in messages if m['role']=='user' and (m.get('metadata') or {}).get('type')!='upload']
    candidates=[m for m in messages if m['role']=='assistant' and any(s.get('metric_ids') or s.get('result_columns')
        for s in ((m.get('metadata') or {}).get('analysis_brief') or {}).get('acceptance',[]))]
    source=candidates[-1] if candidates else None
    state=current if (current or {}).get('analysis_brief',{}).get('calculation_plan') else (source or {}).get('metadata',{})
    brief=state.get('analysis_brief') or {}
    if not brief.get('calculation_plan'):return None
    plan=brief['calculation_plan']
    if table not in {name for s in plan['stages'] for name in [s['source'],*[j['table'] for j in s.get('joins',[])]]}:return None
    question=(current.get('user_request') or current.get('question','')) if state is current else next(
        (m['content'] for m in reversed(messages) if m['role']=='user' and m['id']<source['id'] and (m.get('metadata') or {}).get('type')!='upload'),'')
    bodies=[*users,question]
    if not all(any(quote in text for text in bodies) for quote in plan['source_quotes']):return None
    if any(s.get('intent_gap') for s in brief.get('acceptance',[])) or any(d['status']!='ready' for d in brief.get('metric_dependencies',[])):return None
    return {'brief':deepcopy(calculation_brief(brief)),'question':question,
        'confirmed':[quote for quote in plan['source_quotes'] if any(quote in text for text in bodies)],
        'source_message_id':None if state is current else source['id'],'goal':brief.get('goal',question)}


def _evaluate(dataset,reference,table,frame=None,replacement=None):
    isolated=SessionDataset(session_id=dataset.session_id,parquet_dir=dataset.parquet_dir,
        files=[replacement if replacement and f.table_name==table else f for f in dataset.files])
    try:
        if frame is not None:isolated.get_connection().register(table,frame)
        brief=reference['brief'];plan=CalculationPlan.model_validate(brief['calculation_plan'])
        from app.temporal_scope import verify_temporal_scope
        verify_temporal_scope(brief,reference['question'])
        requested=list(dict.fromkeys(c for s in brief.get('acceptance',[]) for c in s.get('result_columns',[])))
        # Compare terminal metrics and retain separately requested intermediate sample tables.
        from app.business_contract_binding import contract_compile_inputs
        sources,windows=contract_compile_inputs(brief,dataset.session_id,isolated)
        confirmed=[*reference['confirmed'],*sources]
        query,columns=compile_plan(isolated,plan,reference['question'],confirmed,authorized_windows=windows)
        result=isolated.execute(query,as_frame=True)
        if len(result)>50:raise ValueError('本轮结果超过50行，暂不生成完整清洗影响对照；未截取后冒充全量。')
        if any(not pd.api.types.is_scalar(value) for row in result.to_dict('records') for value in row.values()):
            raise ValueError('本轮结果包含嵌套值，暂不支持指标逐项对照。')
        from app.analysis_readiness import _input_coverage
        coverage=_input_coverage(isolated,plan,reference['question'],reference['confirmed'])
        from app.agent.nodes import _sanitize_records
        samples=[];checks=deepcopy(isolated.last_query_checks)
        for spec in brief.get('acceptance',[]):
            sample_fields=spec.get('result_columns',[])
            if not sample_fields or set(sample_fields)<=set(columns):continue
            matches=[i for i,s in enumerate(plan.stages) if set(sample_fields)<={e.name for e in s.select}]
            if not matches:raise ValueError('声明计划没有覆盖所要求的样本列，未把指标表当样本。')
            index=matches[-1];sample_plan=plan.model_copy(update={'stages':plan.stages[:index+1],'row_count':None,'ordered_rows':False})
            sample_sql,sample_columns=compile_plan(isolated,sample_plan,reference['question'],confirmed,sample_fields,authorized_windows=windows)
            sample=isolated.execute(sample_sql,as_frame=True)
            if len(sample)>50:raise ValueError('样本对照超过50行，未截取冒充完整样本。')
            samples.append({'stage':plan.stages[index].name,'columns':sample_columns,'rows':_sanitize_records(sample.to_dict('records'))})
        return {'status':'computed','columns':columns,'rows':_sanitize_records(result.to_dict('records')),
            'numeric_columns':[c for c in columns if pd.api.types.is_numeric_dtype(result[c]) and not pd.api.types.is_bool_dtype(result[c])],
            'input_coverage':coverage,'checks':checks,'samples':samples}
    except (ValueError,duckdb.Error) as exc:
        return {'status':'unverified','reason':str(exc).splitlines()[0][:500],'rows':[],'input_coverage':None}
    finally:isolated.close()


def _changes(reference,before,after):
    plan=reference['brief']['calculation_plan'];stages={s['name']:s for s in plan['stages']}
    def measure(stage,name):
        item=next(e for e in stage['select'] if e['name']==name)
        if item['op']!='column':return True
        owner,_,column=item.get('field','').rpartition('.')
        owner=owner or stage['source'];column=column or item.get('field','')
        return measure(stages[owner],column) if owner in stages else False
    metrics=[c for c in before['columns'] if measure(plan['stages'][-1],c)]
    dimensions=[c for c in before['columns'] if c not in metrics]
    if not metrics:
        numeric=set(before['numeric_columns'])|set(after['numeric_columns'])
        def statistics(side):
            result={'样本行数':len(side['rows'])}
            for column in side['columns']:
                values=[r[column] for r in side['rows'] if r[column] is not None]
                result[column+' · 未知值数']=len(side['rows'])-len(values)
                if column in numeric and not re.search(r'(^id$|_id$|编号|标识)',column,re.I):
                    result[column+' · 已知值小计（样本统计）']=sum((Decimal(str(v)) for v in values),Decimal(0)) if values and column in side['numeric_columns'] else None
            return result
        left,right=statistics(before),statistics(after)
        return [{'dimensions':{},'metric':name,'before':float(x) if isinstance(x,Decimal) else x,
            'after':float(right[name]) if isinstance(right[name],Decimal) else right[name],
            'delta':format(Decimal(str(right[name]))-Decimal(str(x)),'f') if x is not None and right[name] is not None else None,'row_status':'changed'}
            for name,x in left.items() if x!=right.get(name)]
    def index(rows):
        keyed={tuple((type(r[c]).__name__,str(r[c])) for c in dimensions):r for r in rows}
        if len(keyed)!=len(rows):raise ValueError('结果维度不唯一，无法可靠匹配清洗前后指标。')
        return keyed
    left,right=index(before['rows']),index(after['rows']);changes=[]
    for key in dict.fromkeys([*left,*right]):
        a,b=left.get(key),right.get(key)
        for metric in metrics:
            x,y=(a or {}).get(metric),(b or {}).get(metric)
            if a is not None and b is not None and x==y:continue
            numeric=metric in before['numeric_columns'] and metric in after['numeric_columns']
            delta=format(Decimal(str(y))-Decimal(str(x)),'f') if numeric and x is not None and y is not None else None
            changes.append({'dimensions':{c:(b or a)[c] for c in dimensions},'metric':metric,'before':x,'after':y,
                'delta':delta,'row_status':'added' if a is None else 'removed' if b is None else 'changed'})
    return changes


def compare_analysis(dataset,table,reference,*,frame=None,replacement=None,previous=None):
    phase='applied' if replacement else 'preview'
    if not reference:return {'status':'unverified','phase':phase,'reason':'没有可复用的、用户依据明确且无待定口径的本轮计算计划，未推断清洗的业务指标影响。'}
    before=deepcopy(previous['before']) if previous else _evaluate(dataset,reference,table)
    after=_evaluate(dataset,reference,table,frame=frame,replacement=replacement)
    plan=reference['brief']['calculation_plan']
    report={'phase':phase,'source_message_id':reference['source_message_id'],'goal':reference['goal'],
        'plan_sha256':hashlib.sha256(json.dumps(plan,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
        'before':before,'after':after,'status':'partial','changes':[],
        'scope':'使用同一份声明计划、筛选和口径重新试算；一致性不证明清洗规则或业务解释正确，未知值不当零。'}
    if before['status']=='computed' and after['status']=='computed':
        try:
            changes=_changes(reference,before,after)
            report.update(status='verified',changes=changes[:50],change_count=len(changes))
            if all(e['op']=='column' for e in plan['stages'][-1]['select']):
                report['scope']+=' 样本字段统计仅描述结果行数、未知值及已知数值小计，不认证经营指标；完整样本见前后结果。'
                for side in (before,after):
                    if not side.get('samples'):side['samples']=[{'stage':plan['stages'][-1]['name'],'columns':side['columns'],'rows':side['rows']}]
        except ValueError as exc:report.update(status='unverified',reason=str(exc))
    else:
        report['reason']='前后至少一侧无法按该计划计算，未生成差额；可用的一侧仅作为试算结果。'
        if before['status']==after['status']=='unverified':report['status']='unverified'
    if previous:report['preview_agrees']=after==previous['after']
    return report
