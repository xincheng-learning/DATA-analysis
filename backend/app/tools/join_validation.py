"""Reject demonstrated row fan-out for single-source SQL aggregates."""
import json
import uuid
from copy import deepcopy

import duckdb


def _literal(value):
    return "'" + value.replace("'", "''") + "'"


def _quote(value):
    return '"' + value.replace('"', '""') + '"'


def _parse(con, sql):
    return json.loads(con.execute('SELECT json_serialize_sql(' + _literal(sql) + ')').fetchone()[0])['statements'][0]['node']


def _sql(con, node):
    document = json.dumps({'error': False, 'statements': [{'node': node}]})
    return con.execute('SELECT json_deserialize_sql(' + _literal(document) + ')').fetchone()[0]


def _expressions(value):
    if isinstance(value, dict):
        if value.get('type') == 'SELECT_NODE':
            return  # Subquery expressions belong to their own scope.
        yield value
        for child in value.values():
            yield from _expressions(child)
    elif isinstance(value, list):
        for child in value:
            yield from _expressions(child)


def _sources(table):
    if table['type'] == 'JOIN' and not table.get('alias'):
        return [*_sources(table['left']), *_sources(table['right'])]
    return [table]


def validate_join_aggregates(con, sql):
    checked=[]
    # shortcut: checks SELECT scopes and simple projection lineage; filtered
    # derived scopes and cross-source weighted formulas still need business review.
    if 'join' not in sql.lower() and ',' not in sql:
        return checked
    root = _parse(con, sql)
    identity = '__analysis_row_' + uuid.uuid4().hex

    def inspect(node, ctes, expanded=frozenset()):
        table = node.get('from_table', {})
        if table.get('type') != 'JOIN':
            name = table.get('table_name', '').casefold()
            entry = next((item for key, item in ctes.items() if key.casefold() == name), None)
            origin = table.get('subquery') if table.get('type') == 'SUBQUERY' else entry['value']['query'] if entry else None
            if not origin or name and name in expanded or node.get('where_clause'):
                return
            origin = origin['node']
            if origin['type'] != 'SELECT_NODE' or origin['group_expressions'] or origin['modifiers']:
                return
            projection = {item.get('alias') or (item['column_names'][-1] if item.get('class') == 'COLUMN_REF' else ''): item
                          for item in origin['select_list']}
            pushed = []
            for aggregate in _expressions(node['select_list']):
                if aggregate.get('class') != 'FUNCTION' or aggregate.get('function_name', '').lower() not in {'sum', 'avg', 'count'} or aggregate.get('distinct') or aggregate.get('filter'):
                    continue
                children = aggregate['children']
                column = children[0]['column_names'][-1] if len(children) == 1 and children[0].get('class') == 'COLUMN_REF' else ''
                source = projection.get(column)
                if source and source.get('class') == 'COLUMN_REF':
                    pushed.append({**deepcopy(aggregate), 'children': [deepcopy(source)]})
            if pushed:
                scope = {**ctes, **{item['key']: item for item in origin['cte_map']['map']}}
                inspect({**origin, 'select_list': pushed}, scope, expanded | {name})
            return
        sources = _sources(table)
        aliases = {source.get('alias') or source.get('table_name'): source for source in sources}
        if None in aliases or len(aliases) != len(sources):
            return
        matched_aliases = {alias.casefold(): alias for alias in aliases}
        columns = {}

        def scope_query(projection, source):
            query = _parse(con, 'SELECT ' + projection + ' FROM _placeholder')
            query['from_table'] = deepcopy(source)
            query['cte_map']['map'] = list(ctes.values())
            return query

        checks = []
        for expression in _expressions([*node['select_list'], node.get('having')]):
            if expression.get('class') != 'FUNCTION' or expression.get('function_name', '').lower() not in {'sum', 'avg', 'count'} or expression.get('distinct'):
                continue
            owners = set()
            references = [item['column_names'] for item in _expressions(expression['children']) if item.get('class') == 'COLUMN_REF']
            for names in references:
                if len(names) > 1:
                    owner = matched_aliases.get(names[-2].casefold())
                else:
                    for alias, source in aliases.items():
                        if alias not in columns:
                            query = _sql(con, scope_query('*', source))
                            columns[alias] = {row[0].casefold() for row in con.execute('DESCRIBE ' + query).fetchall()}
                    candidates = [alias for alias in aliases if names[0].casefold() in columns[alias]]
                    owner = candidates[0] if len(candidates) == 1 else None
                owners.add(owner)
            if len(owners) == 1 and None not in owners:
                checks.append((owners.pop(), expression.get('filter')))
        if not checks:
            return

        probe = deepcopy(node)
        probe.update(cte_map={'map': list(ctes.values())}, modifiers=[], group_expressions=[], group_sets=[],
                     aggregate_handling='STANDARD_HANDLING', having=None, qualify=None)
        probe['select_list'] = []
        for alias, condition in checks:
            reference = _quote(alias) + '.' + _quote(identity)
            counts = _parse(con, f'SELECT count({reference}), count(DISTINCT {reference}) FROM _placeholder')['select_list']
            for count in counts:
                count['filter'] = deepcopy(condition)
            probe['select_list'].extend(counts)

        def tag(source):
            if source['type'] == 'JOIN' and not source.get('alias'):
                source['left'], source['right'] = tag(source['left']), tag(source['right'])
                return source
            alias = source.get('alias') or source.get('table_name')
            if alias not in {check[0] for check in checks}:
                return source
            wrapper = _parse(con, f'SELECT *, row_number() OVER () AS {_quote(identity)} FROM _placeholder')
            wrapper['from_table'] = source
            derived = _parse(con, f'SELECT * FROM (SELECT 1) AS {_quote(alias)}')['from_table']
            derived['subquery']['node'] = wrapper
            return derived

        probe['from_table'] = tag(probe['from_table'])
        try:
            counts = con.execute(_sql(con, probe)).fetchone()
        except duckdb.BinderException:
            # Correlated outer references cannot be evaluated as a separate scope.
            return
        for index, (alias, _) in enumerate(checks):
            repeated, original = counts[index * 2:index * 2 + 2]
            if repeated > original:
                raise ValueError(f'关联后来源 {alias} 的 {original} 行被重复为 {repeated} 行，直接 SUM/AVG/COUNT 会重复汇总。'
                                 '请先按目标业务粒度分别聚合再关联，或用 EXISTS 筛选；不要用 SUM(DISTINCT 金额) 去重不同订单。')
            checked.append({'source':alias,'joined_rows':repeated,'source_rows':original})

    def visit(value, ctes):
        if isinstance(value, dict):
            if value.get('type') == 'SELECT_NODE':
                ctes = {**ctes, **{item['key']: item for item in value['cte_map']['map']}}
                for child in value.values():
                    visit(child, ctes)
                inspect(value, ctes)
            else:
                for child in value.values():
                    visit(child, ctes)
        elif isinstance(value, list):
            for child in value:
                visit(child, ctes)

    visit(root, {})
    return checked
