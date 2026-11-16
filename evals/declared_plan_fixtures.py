"""Authored intent contracts for controlled gate tests, NOT model-generated plans.

Only table/field roles, scope and operations appear here: no teacher numbers or SQL.
Real model runs must generate their own plans from public questions and schema.
"""
from copy import deepcopy


def expression(name, op, field='', **extra):
    return {'name': name, 'op': op, 'field': field, **extra}


def predicate(field, op, value=None, cast=''):
    return {'field': field, 'op': op, 'value': value, 'cast': cast}


def stage(name, source, select, **extra):
    return {'name': name, 'source': source, 'select': select, **extra}


def join(table, left, right, kind='inner'):
    return {'table': table, 'left_keys': left, 'right_keys': right, 'kind': kind}


def declared_plan(family, question):
    january = [predicate('orders.status', 'eq', 'completed'),
        predicate('orders.ordered_at', 'ge', '2026-01-01', 'timestamp'),
        predicate('orders.ordered_at', 'lt', '2026-02-01', 'timestamp')]
    filtered = stage('selected_orders', 'orders', [expression(name, 'column', 'orders.'+name)
        for name in ('order_id', 'customer_id', 'region')], filters=january)
    totals = stage('order_totals', 'selected_orders', [
        *[expression(name, 'column', 'selected_orders.'+name) for name in ('order_id', 'customer_id', 'region')],
        expression('amount', 'sum', 'lines.amount')],
        joins=[join('lines', ['selected_orders.order_id'], ['lines.order_id'])],
        group_by=['selected_orders.'+name for name in ('order_id', 'customer_id', 'region')])
    base = [filtered, totals]
    plan = {'source_quotes': [question], 'stages': [], 'ordered_rows': False, 'ordered_columns': False}
    if family == 'join_target':
        targets = stage('monthly_targets', 'targets', [expression(name, 'column', 'targets.'+name) for name in ('region', 'target')],
                        filters=[predicate('targets.month', 'eq', '2026-01')])
        result = stage('region_result', 'order_totals', [expression('region', 'column', 'order_totals.region'),
            expression('amount', 'sum', 'order_totals.amount'), expression('target', 'max', 'monthly_targets.target')],
            joins=[join('monthly_targets', ['order_totals.region'], ['monthly_targets.region'], 'left')], group_by=['order_totals.region'])
        plan['stages'] = [*base, targets, result]
    elif family == 'order_grain':
        plan['stages'] = [filtered, stage('summary', 'selected_orders', [expression('order_count', 'count_rows')])]
    elif family == 'order_denominator':
        plan['stages'] = [*base, stage('summary', 'order_totals', [expression('average_amount', 'mean', 'order_totals.amount')])]
    elif family == 'missing_target':
        plan['stages'] = [stage('summary', 'targets', [expression('target', 'max', 'targets.target')],
            filters=[predicate('targets.month', 'eq', '2026-01'), predicate('targets.region', 'eq', '华北')])]
        plan['row_count'] = 1
    elif family == 'zero_denominator':
        actual = stage('actual', 'orders', [expression('region', 'column', 'orders.region'), expression('amount', 'sum', 'lines.amount')],
            joins=[join('lines', ['orders.order_id'], ['lines.order_id'])], group_by=['orders.region'], filters=[
                predicate('orders.status', 'eq', 'completed'), predicate('orders.ordered_at', 'ge', '2026-02-01', 'timestamp'),
                predicate('orders.ordered_at', 'lt', '2026-03-01', 'timestamp')])
        target = stage('target_scope', 'targets', [expression(name, 'column', 'targets.'+name) for name in ('region', 'target')],
            filters=[predicate('targets.month', 'eq', '2026-02'), predicate('targets.region', 'eq', '华东')])
        combined = stage('combined', 'target_scope', [expression('amount', 'column', 'actual.amount'), expression('target', 'column', 'target_scope.target')],
            joins=[join('actual', ['target_scope.region'], ['actual.region'], 'left')])
        plan['stages'] = [actual, target, combined, stage('summary', 'combined', [
            expression('attainment_pct', 'ratio', numerator='combined.amount', denominator='combined.target', multiplier=100)])]
        plan.update(row_count=1, ordered_columns=True)
    elif family == 'refund_status':
        plan['stages'] = [stage('summary', 'refunds', [expression('refund_amount', 'sum', 'refunds.amount')],
            filters=[predicate('refunds.status', 'eq', 'settled')])]
    elif family == 'repeat_denominator':
        counts = stage('buyer_counts', 'selected_orders', [expression('customer_id', 'column', 'selected_orders.customer_id'),
            expression('n', 'count_rows')], group_by=['selected_orders.customer_id'])
        overall = stage('counts', 'buyer_counts', [expression('repeaters', 'count_if', condition=predicate('buyer_counts.n', 'ge', 2)),
            expression('buyers', 'count_rows')])
        plan['stages'] = [filtered, counts, overall, stage('summary', 'counts', [
            expression('repeat_pct', 'ratio', numerator='counts.repeaters', denominator='counts.buyers', multiplier=100)])]
    elif family == 'month_endpoint':
        plan['stages'] = [*base, stage('summary', 'order_totals', [expression('amount', 'sum', 'order_totals.amount')])]
    elif family == 'null_mean':
        plan['stages'] = [stage('summary', 'observations', [expression('mean', 'mean', 'observations.value')])]
    elif family == 'duplicate_observation':
        plan['stages'] = [stage('raw', 'observations', [expression('value', 'column', 'observations.value')],
                               order_by=[{'field': 'observations.record_id'}])]
        plan['ordered_rows'] = True
    elif family == 'legitimate_empty':
        plan['stages'] = [stage('raw', 'orders', [expression('order_id', 'column', 'orders.order_id')],
            filters=[predicate('orders.region', 'eq', '西区'), predicate('orders.status', 'eq', 'completed')])]
    elif family == 'tiny_sample_statistics':
        plan['stages'] = [stage('summary', 'observations', [expression('n', 'count', 'observations.tiny'),
            expression('mean', 'mean', 'observations.tiny'), expression('std', 'std_samp', 'observations.tiny')])]
        plan.update(row_count=1, ordered_columns=True)
    else:
        raise ValueError('No authored plan for this controlled fixture')
    return deepcopy(plan)
