from types import SimpleNamespace

import pytest

from app.answer_acceptance import prepare_brief, assess_coverage, dependency_answer


@pytest.mark.parametrize('quoted,answered', [('这是合成观测，不代表真实业务总体。',True),('这是随机抽样的真实客户。',False)])
def test_scope_declaration_uses_confirmed_user_source_not_execution_proof(quoted,answered):
    from app.schemas import RequirementSpec
    spec=RequirementSpec(requirement_index=0,answer_type='scope',subject='数据来源声明',scope_key='population').model_dump()
    b={'requirements':['说明数据来源边界'],'acceptance':[spec],
        'scope_sources':[{'key':'population','text':quoted}]}
    prepared=prepare_brief({'question':'这是合成观测，不代表真实业务总体。'},b)
    coverage=assess_coverage(prepared,{'evidence':[],'findings':[],'coverage':[]})
    assert (coverage[0]['status']=='answered')==answered
    if answered: assert '用户声明' in coverage[0]['reason']


def test_compatible_constraint_fragments_merge_without_discarding_a_user_instruction():
    from app.schemas import AnalysisBrief
    b=AnalysisBrief(goal='统计',requirements=['输出格式与执行限制'],acceptance=[
        {'requirement_index':0,'answer_type':'constraint','subject':'只输出一行统计表'},
        {'requirement_index':0,'answer_type':'constraint','subject':'不清洗、不筛行、不绘图'}])
    assert len(b.acceptance)==1
    assert '只输出一行统计表' in b.acceptance[0].subject and '不清洗、不筛行、不绘图' in b.acceptance[0].subject
    assert not b.unverified_constraints


def test_conflicting_duplicate_criteria_are_still_rejected():
    from app.schemas import AnalysisBrief
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AnalysisBrief(goal='统计',requirements=['统计表'],acceptance=[
            {'requirement_index':0,'answer_type':'table','result_columns':['n']},
            {'requirement_index':0,'answer_type':'table','result_columns':['mean']}])


def test_extra_scope_criterion_reuses_an_existing_quoted_source_without_becoming_a_suggestion():
    from app.schemas import AnalysisBrief
    b=AnalysisBrief(goal='统计',requirements=['统计表'],acceptance=[
        {'requirement_index':0,'answer_type':'table','result_columns':['n']},
        {'requirement_index':1,'answer_type':'scope','scope_key':'population'}],
        scope_sources=[{'key':'population','text':'这是合成观测。'}])
    assert len(b.acceptance)==1 and not b.unverified_constraints
    assert b.scope_sources[0].text=='这是合成观测。'


def test_numeric_fallback_never_claims_a_value_from_an_empty_result_schema():
    b=prepare_brief({'question':'数值'},brief('value',columns=['custom_value']))
    r={'status':'fallback','coverage':[],'evidence':[{'id':'S1.E1','kind':'execution_receipt','step':1,
        'requirement_ids':['R1'],'columns':['custom_value'],'row_count':0,'complete_result_available':True}]}
    assert assess_coverage(b,r)[0]['status']!='answered'


def test_constraint_merge_keeps_the_original_text_length_boundary():
    from app.schemas import AnalysisBrief
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AnalysisBrief(goal='限制',requirements=['限制'],acceptance=[
            {'requirement_index':0,'answer_type':'constraint','subject':'a'*700},
            {'requirement_index':0,'answer_type':'constraint','subject':'b'*700}])


def test_scope_acknowledgement_cannot_hide_a_numeric_target():
    from app.schemas import RequirementSpec
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        RequirementSpec(requirement_index=0,answer_type='scope',scope_key='population',result_columns=['profit'])


def brief(kind='comparison',metrics=None,columns=None):
    return {'goal':'比较','requirements':['本轮要求'],'confirmed_scope':[],
        'acceptance':[{'requirement_index':0,'answer_type':kind,'metric_ids':metrics or [],'result_columns':columns or []}]}


def report(kind='generated'):
    return {'status':kind,'findings':[{'text':'商品金额上升。','reason':'来自相同范围的比较。','evidence_ids':['S1.F4']}],
        'coverage':[{'requirement':'本轮要求','status':'answered','evidence_ids':['S1.F4']}],
        'evidence':[{'id':'S1.F4','kind':'change','metric':'item_amount_brl','metric_id':'item_amount','step':1},
            {'id':'S1.E1','kind':'execution_receipt','step':1,'columns':['item_amount_brl'],
                'metric_ids':['item_amount'],'verified_metric_ids':['item_amount'],'requirement_ids':['R1'],'row_count':2,'retained_rows':2,'complete_result_available':True}]}


def test_statistics_routes_keep_original_failure_and_accept_explicit_method_objects():
    from pathlib import Path
    from app.agent.router import RouteDecision
    from app.schemas import RequirementSpec
    original=Path(__file__).with_name('fixtures')/'statistics-route-1692.json'
    with pytest.raises(ValueError):RouteDecision.model_validate_json(original.read_text('utf-8-sig'))
    current=original.with_name('statistics-route-1694.json')
    route=RouteDecision.model_validate_json(current.read_text('utf-8-sig'))
    assert route.route=='python'
    specs=route.analysis_brief.acceptance
    assert specs[0].result_columns==['n','mean','std','t_stat','p_value']
    assert specs[1].result_columns==['t_stat','p_value'] and specs[3].subject
    for kind in ('value','comparison','table','chart'):
        with pytest.raises(ValueError):RequirementSpec(requirement_index=0,answer_type=kind)


@pytest.mark.parametrize('kind',['definition','feasibility'])
def test_explicit_subject_is_legal_without_fabricating_result_columns_or_financial_dependencies(kind):
    from app.schemas import RequirementSpec
    spec=RequirementSpec(requirement_index=0,answer_type=kind,subject='单样本均值检验的方法定义或适用范围')
    assert not spec.metric_ids and not spec.result_columns
    b=prepare_brief({'question':'方法说明'},{'goal':'方法说明','requirements':['方法说明'],'acceptance':[spec.model_dump()]})
    assert not b['metric_dependencies'] and assess_coverage(b,{})[0]['status']!='answered'


def test_named_method_explanation_must_match_its_own_requirement_and_execution():
    b=prepare_brief({'question':'解释检验假设'},brief('definition'))
    b['acceptance'][0]['subject']='检验假设'
    r={'status':'generated','coverage':[{'requirement_id':'R1','evidence_ids':['S1.E1']}],
       'evidence':[{'id':'S1.E1','kind':'execution_receipt','step':1,'requirement_ids':['R1']}],
       'findings':[{'text':'说明检验假设','reason':'检验均值相对指定基准的差异，不能推断因果。','requirement_ids':['R2'],'evidence_ids':['S1.E1']}]}
    assert assess_coverage(b,r)[0]['status']!='answered'
    r['findings'][0]['requirement_ids']=['R1']
    assert assess_coverage(b,r)[0]['status']=='answered'
    r['evidence'][0]['requirement_ids']=['R2']
    assert assess_coverage(b,r)[0]['status']!='answered'
    b['acceptance'][0]['answer_type']='constraint'
    r['evidence'][0]['requirement_ids']=['R1']
    assert assess_coverage(b,r)[0]['status']!='answered'


def test_extra_constraint_is_an_unverified_proposal_and_never_a_user_requirement():
    import json
    from pathlib import Path
    from app.agent.router import RouteDecision
    path=Path(__file__).with_name('fixtures')/'statistics-route-1693.json'
    raw=json.loads(path.read_text('utf-8-sig'))
    route=RouteDecision.model_validate(raw)
    assert len(route.analysis_brief.requirements)==8
    assert route.analysis_brief.unverified_constraints==[raw['analysis_brief']['acceptance'][-1]['subject']]
    from app.answer_acceptance import calculation_brief
    for question,subject in [('上一次的要求是不绘图；这次请绘图。','不绘图'),
        ('允许不清洗，也允许先清洗再分析。','不清洗'),('请计算标准差','不计算标准差')]:
        invented=json.loads(path.read_text('utf-8-sig'))
        invented['analysis_brief']['acceptance'][-1]['subject']=subject
        decision=RouteDecision.model_validate(invented)
        prepared=prepare_brief({'question':question},decision.analysis_brief.model_dump())
        executable=calculation_brief(prepared)
        assert subject not in executable['requirements'] and 'unverified_constraints' not in executable
        assert [spec['result_columns'] for spec in executable['acceptance'][:5]]==[['n'],['mean'],['std'],['t_stat'],['p_value']]
    for change in ({'requirement_index':10},{'answer_type':'value','result_columns':['n']},{'subject':''}):
        changed=json.loads(path.read_text('utf-8-sig'))
        changed['analysis_brief']['acceptance'][-1].update(change)
        with pytest.raises(ValueError):RouteDecision.model_validate(changed)


def test_an_unrelated_real_reference_does_not_answer_profit():
    b=prepare_brief({'question':'利润变化'},brief(metrics=['profit']))
    result=assess_coverage(b,report())
    assert result[0]['linked'] is True and result[0]['status']=='needs_input'
    assert '成本' in result[0]['reason']


def test_matching_comparison_is_answered_but_untyped_legacy_is_only_linked():
    b=prepare_brief({'question':'金额比较'},brief(metrics=['item_amount']))
    assert assess_coverage(b,report())[0]['status']=='answered'
    old=prepare_brief({'question':'比较'},{'goal':'比较','requirements':['比较'],'confirmed_scope':[]})
    assert assess_coverage(old,report())[0]['status']=='linked'
    r=report();r['evidence'][-1]['verified_metric_ids']=[]
    assert assess_coverage(b,r)[0]['status']=='linked'


@pytest.mark.parametrize('kind',['value','table','comparison','explanation'])
def test_failed_calculation_cannot_complete_a_requirement_even_with_a_passing_check(kind):
    b=prepare_brief({'question':'金额'},brief(kind,columns=['item_amount_brl']))
    r=report()
    r['evidence'][-1]['checks']=[{'id':'calculation_contract','status':status,'requirement_ids':['R1']}
        for status in ['passed','failed']]
    item=assess_coverage(b,r)[0]
    assert item['status']=='linked' and '核对未通过' in item['reason']


def test_facts_only_cannot_complete_explanation_and_incomplete_table_cannot_be_delivered():
    b=prepare_brief({'question':'解释'},brief('explanation',metrics=['item_amount']))
    assert assess_coverage(b,report('fallback'))[0]['status']=='linked'
    b=prepare_brief({'question':'完整表'},brief('table',columns=['item_amount_brl']))
    r=report();r['evidence'][-1].update(row_count=51,retained_rows=50,complete_result_available=False)
    assert assess_coverage(b,r)[0]['status']=='linked'
    r['evidence'][-1]['complete_result_available']=True
    assert assess_coverage(b,r)[0]['status']=='answered'


def test_scope_changes_require_real_user_sources_and_preserve_other_confirmations():
    old={'role':'assistant','metadata':{'analysis_brief':{'scope_sources':[
        {'key':'currency','text':'使用BRL','source_question':'使用BRL，仅看五月','message_id':None},
        {'key':'period','text':'五月','source_question':'使用BRL，仅看五月','message_id':None}]}}}
    messages=[{'id':1,'role':'user','content':'使用BRL，仅看五月'},old]
    b=brief();b['scope_sources']=[{'key':'period','text':'六月','message_id':None},
        {'key':'population','text':'已送达','message_id':None}]
    b=prepare_brief({'question':'改为六月'},b,messages=messages)
    assert {(s['key'],s['text']) for s in b['scope_sources']}=={('currency','使用BRL'),('period','六月'),('selected_period','六月')}


def test_confirmed_income_cost_and_policy_dependencies_can_be_supplied():
    question='sales.amount是收入，costs.cogs是实际成本，利润按收入减实际成本计算'
    b=brief(metrics=['profit']);b['scope_sources']=[{'key':'profit_policy','text':'利润按收入减实际成本计算','message_id':None}]
    b['metric_bindings']=[{'role':'revenue','columns':['sales.amount'],'confirmation':{'key':'revenue','text':'sales.amount是收入','message_id':None}},
        {'role':'costs','columns':['costs.cogs'],'confirmation':{'key':'costs','text':'costs.cogs是实际成本','message_id':None}}]
    dataset=SimpleNamespace(files=[SimpleNamespace(table_name='sales',columns=['amount']),SimpleNamespace(table_name='costs',columns=['cogs'])])
    accepted=prepare_brief({'question':question},b,dataset=dataset)
    assert accepted['metric_dependencies'][0]['status']=='ready'
    dataset.files.pop()
    blocked=prepare_brief({'question':question},b,dataset=dataset)
    assert blocked['metric_dependencies'][0]['status']=='needs_input'


def test_a_profit_alias_does_not_prove_the_financial_definition():
    b=prepare_brief({'question':'计算利润'},brief('value',columns=['profit_brl']))
    b['metric_dependencies'][0]['status']='ready'
    r=report();r['evidence'][0].update(metric='profit_brl',metric_id='profit')
    r['evidence'][-1].update(columns=['profit_brl'],metric_ids=['profit'],verified_metric_ids=[],sql='SELECT amount AS profit_brl FROM sales')
    item=assess_coverage(b,r)[0]
    assert item['status']=='linked' and '口径' in item['reason']


def test_a_correct_feasibility_answer_can_be_complete_without_a_profit_value():
    b=prepare_brief({'question':'利润可以算吗，缺什么'},brief('feasibility',metrics=['profit']))
    assert assess_coverage(b,{'dependency_response':'unrelated canonical response'})[0]['status']=='needs_input'
    item=assess_coverage(b,{'dependency_response':dependency_answer(b)})[0]
    assert item['status']=='answered' and not item['linked']


def test_a_chart_has_to_cover_the_required_columns_and_this_requirement():
    b=prepare_brief({'question':'画三个指标'},brief('chart',columns=['amount','orders','average']))
    r={'status':'generated','coverage':[{'requirement':'本轮要求','evidence_ids':['S1.V1']}],
        'evidence':[{'id':'S1.V1','kind':'chart_artifact','step':1,'columns':['amount','orders']},
            {'id':'S1.E1','kind':'execution_receipt','step':1,'columns':['amount','orders','average'],'requirement_ids':['R1']}]}
    assert assess_coverage(b,r)[0]['status']=='linked'
    r['evidence'][0]['columns'].append('average')
    assert assess_coverage(b,r)[0]['status']=='answered'
    r['evidence'][-1]['requirement_ids']=['R2']
    assert assess_coverage(b,r)[0]['status']=='linked'


@pytest.mark.parametrize('kind',['value','comparison','table','explanation','chart'])
def test_same_column_from_another_requirement_cannot_complete_this_one(kind):
    b=prepare_brief({'question':'五月amount'},brief(kind,columns=['amount']))
    r={'status':'generated','findings':[{'reason':'六月的比较','evidence_ids':['S1.F1']}],
       'coverage':[{'requirement_id':'R1','evidence_ids':['S1.F1']}],
       'evidence':[{'id':'S1.F1','step':1,'kind':'change','metric':'amount','columns':['amount']},
           {'id':'S1.E1','step':1,'kind':'execution_receipt','columns':['amount'],
            'requirement_ids':['R2'],'complete_result_available':True}]}
    assert assess_coverage(b,r)[0]['status']=='linked'


def test_mixed_sources_cannot_launder_columns_from_another_requirement():
    b=prepare_brief({'question':'五月amount'},brief('table',columns=['amount']))
    r={'coverage':[{'requirement_id':'R1','evidence_ids':['S1.E1','S2.E1']}], 'evidence':[
        {'id':'S1.E1','step':1,'kind':'execution_receipt','columns':['other'],'requirement_ids':['R1'],'complete_result_available':True},
        {'id':'S2.E1','step':2,'kind':'execution_receipt','columns':['amount'],'requirement_ids':['R2'],'complete_result_available':True}]}
    assert assess_coverage(b,r)[0]['status']=='linked'


def test_another_requirements_reason_cannot_complete_this_explanation():
    b=prepare_brief({'question':'解释amount'},brief('explanation',columns=['amount']))
    r={'status':'generated','coverage':[{'requirement_id':'R1','evidence_ids':['S1.R1','S2.R1']}],
        'findings':[{'reason':'仅解释第二项','evidence_ids':['S2.R1']}], 'evidence':[
        {'id':'S1.R1','step':1,'kind':'row','columns':['amount']},
        {'id':'S1.E1','step':1,'kind':'execution_receipt','columns':['amount'],'requirement_ids':['R1']},
        {'id':'S2.R1','step':2,'kind':'row','columns':['amount']},
        {'id':'S2.E1','step':2,'kind':'execution_receipt','columns':['amount'],'requirement_ids':['R2']}]}
    assert assess_coverage(b,r)[0]['status']=='linked'
    r['findings'][0]['evidence_ids']=['S1.R1']
    assert assess_coverage(b,r)[0]['status']=='answered'
