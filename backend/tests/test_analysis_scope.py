from copy import deepcopy

from app.analysis_scope import create_scope, finish_scope, scope_history


def brief(period='五月',metric='item_amount',revision=''):
    return {'scope_sources':[{'key':'period','text':period}],
        'acceptance':[{'id':'R1','answer_type':'value','metric_ids':[metric],'result_columns':['amount']}],
        'revision_quote':revision}


def message(mid,scope):
    return {'id':mid,'role':'assistant','content':'原始结果 40','metadata':{'analysis_scope':scope,'rows':[{'amount':40}],
        'columns':['amount'],'analysis_brief':brief(),'chart_spec':{'data':[{'y':[40]}]}}}


def ready(scope):
    return finish_scope(scope,{'rows':[{'amount':40}],'columns':['amount']})


def test_revision_keeps_original_values_and_marks_only_its_own_lineage():
    old=message(1,ready(create_scope(brief(),[],data_version='v1',table_versions={'orders':'o1'})))
    unrelated=message(2,ready(create_scope(brief(metric='delivered_orders'),[],data_version='v1',table_versions={'orders':'o1'})))
    second=create_scope(brief('六月',revision='改成六月'),[old],data_version='v1',table_versions={'orders':'o1'})
    new=message(3,ready(second));before=deepcopy([old,unrelated,new])
    history=scope_history([old,unrelated,new],table_versions={'orders':'o1'})
    assert history[0]['metadata']['analysis_scope_state']['status']=='historical_scope'
    assert '时间范围' in history[0]['metadata']['analysis_scope_state']['changed_fields']
    assert history[1]['metadata']['analysis_scope_state']['status']=='current'
    assert history[0]['metadata']['rows']==[{'amount':40}] and [old,unrelated,new]==before


def test_new_question_does_not_retire_previous_results():
    old=message(1,ready(create_scope(brief(),[],data_version='v1',table_versions={'orders':'o1'})))
    profit=message(2,finish_scope(create_scope(brief(metric='profit'),[old],data_version='v1',table_versions={'orders':'o1'}),{}))
    assert not profit['metadata']['analysis_scope']['revises_message_ids']
    assert scope_history([old,profit])[0]['metadata']['analysis_scope_state']['status']=='current'


def test_failed_revision_and_its_successor_preserve_transitive_history():
    old=message(1,ready(create_scope(brief(),[],data_version='v1',table_versions={'orders':'o1'})))
    failed=message(2,finish_scope(create_scope(brief('六月',revision='改成六月'),[old],data_version='v1',table_versions={'orders':'o1'}),{'error':'执行失败'}))
    history=scope_history([old,failed])
    assert history[0]['metadata']['analysis_scope_state']['recalculation_status']=='failed'
    newer=message(3,ready(create_scope(brief('六月',revision='重新计算'),[old,failed],data_version='v1',table_versions={'orders':'o1'})))
    history=scope_history([old,failed,newer])
    assert history[0]['metadata']['analysis_scope_state']['replacement_message_id']==3
    assert '时间范围' in history[0]['metadata']['analysis_scope_state']['changed_fields']
    assert history[1]['metadata']['analysis_scope_state']['recalculation_status']=='complete'


def test_partial_child_results_cannot_hide_a_failed_final_recomputation():
    scope=create_scope(brief(),[],data_version='v1',table_versions={'orders':'o1'})
    final={'error':'报告中断','workflow':{'status':'completed','steps':[{'status':'succeeded','request':'金额',
        'result':{'columns':['amount'],'rows':[{'amount':40}]}}]}}
    assert finish_scope(scope,final)['result_status']=='failed'


def test_data_changes_check_only_tables_used_by_the_declared_calculation():
    b=brief();b['calculation_plan']={'stages':[{'source':'orders','joins':[]}]}
    scope=ready(create_scope(b,[],data_version='v1',table_versions={'orders':'o1','unrelated':'u1'}))
    assert scope['table_versions']=={'orders':'o1'}
    old=message(1,scope)
    assert scope_history([old],table_versions={'orders':'o1','unrelated':'u2'})[0]['metadata']['analysis_scope_state']['status']=='current'
    assert scope_history([old],table_versions={'orders':'o2'})[0]['metadata']['analysis_scope_state']['status']=='data_changed'
    assert scope_history([old],definition_version='new-definitions')[0]['metadata']['analysis_scope_state']['status']=='definition_changed'


def test_retired_numbers_and_charts_are_not_projected_as_current_model_evidence():
    from app.context_memory import project_messages
    old=message(1,ready(create_scope(brief(),[],data_version='v1',table_versions={'orders':'o1'})))
    new=message(2,ready(create_scope(brief('六月',revision='改成六月'),[old],data_version='v1',table_versions={'orders':'o1'})))
    records=project_messages([old,new])
    assert '40' not in records[0]['content']
    assert not records[0].get('chart_snapshot') and not records[0]['evidence'].get('computed')
    assert records[0]['evidence']['analysis_scope_state']['status']=='historical_scope'
