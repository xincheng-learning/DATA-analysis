"""Reject observed floating-point boundary ambiguity without changing source values."""
from copy import deepcopy

import duckdb

from app.tools.join_validation import _parse, _sql, _expressions


def validate_numeric_boundaries(con, sql):
    checked = []
    root = _parse(con, sql)
    true = _parse(con, 'SELECT true')['select_list'][0]

    def inspect(node, ctes):
        aliases = {item['alias']: item for item in node['select_list'] if item.get('alias')}

        def expand(value, seen=frozenset()):
            if isinstance(value, list):
                return [expand(child, seen) for child in value]
            if not isinstance(value, dict):
                return value
            names = value.get('column_names', [])
            if value.get('class') == 'COLUMN_REF' and len(names) == 1 and names[0] in aliases and names[0] not in seen:
                return {**expand(aliases[names[0]], seen | {names[0]}), 'alias': ''}
            return {key: expand(child, seen) for key, child in value.items()}

        for comparison in _expressions([node['select_list'], node.get('where_clause'), node.get('having')]):
            if comparison.get('class') != 'COMPARISON' or comparison.get('type') not in {
                'COMPARE_EQUAL', 'COMPARE_NOTEQUAL', 'COMPARE_LESSTHAN', 'COMPARE_GREATERTHAN',
                'COMPARE_LESSTHANOREQUALTO', 'COMPARE_GREATERTHANOREQUALTO',
            }:
                continue
            operands = [expand(comparison[key]) for key in ('left', 'right')]
            probe = deepcopy(node)
            probe.update(cte_map={'map': list(ctes.values())}, modifiers=[], having=None, qualify=None,
                select_list=[{**value, 'alias': alias} for value, alias in zip(operands, ('__left', '__right'))])
            # Probe before this filter can hide the values close to its boundary.
            def remove_current(value):
                if value == comparison:
                    return deepcopy(true)
                if isinstance(value, dict):
                    return {key: remove_current(child) for key, child in value.items()}
                if isinstance(value, list):
                    return [remove_current(child) for child in value]
                return value
            probe['where_clause'] = remove_current(node.get('where_clause'))
            aggregate = any(item.get('function_name', '').lower() in {'sum', 'avg', 'count', 'count_star', 'min', 'max'}
                for item in _expressions(operands))
            if not aggregate:
                probe.update(group_expressions=[], group_sets=[])
            query = _sql(con, probe)
            try:
                types = [row[1] for row in con.execute('DESCRIBE ' + query).fetchall()]
                if not any(kind in {'DOUBLE', 'FLOAT', 'REAL'} for kind in types):
                    continue
                if not all(kind in {'DOUBLE', 'FLOAT', 'REAL', 'TINYINT', 'SMALLINT', 'INTEGER', 'BIGINT', 'HUGEINT',
                    'UTINYINT', 'USMALLINT', 'UINTEGER', 'UBIGINT', 'UHUGEINT'} or kind.startswith('DECIMAL') for kind in types):
                    continue
                # shortcut: a conservative proximity check, not a floating error proof;
                # correlated scopes and other numeric methods still need analyst review.
                near = con.execute('SELECT count(*) FROM (' + query + ''') p WHERE
                    abs(CAST(__left AS DOUBLE)-CAST(__right AS DOUBLE))>0 AND
                    abs(CAST(__left AS DOUBLE)-CAST(__right AS DOUBLE)) <=
                    1e-12*greatest(abs(CAST(__left AS DOUBLE)),abs(CAST(__right AS DOUBLE)),1)''').fetchone()[0]
            except duckdb.BinderException:
                checked.append({'id': 'numeric_boundary', 'status': 'not_checked',
                    'detail': '此相关或派生比较无法独立检查浮点边界；不据此确认计算精度。'})
                continue
            if near:
                raise ValueError(f'浮点比较发现 {near} 行紧邻判断边界，可能把分币差额或应相等的值误判。'
                    '请先确认业务精度：货币应在运算前按已确认的小数位转换为 DECIMAL；'
                    '或在有业务依据时明确 ROUND/容差。不要擅自统一保留两位，也不要只把最终显示值四舍五入。')
            checked.append({'id': 'numeric_boundary', 'status': 'passed',
                'detail': '所检查比较未发现非零差在相对 1e-12 范围内的浮点边界值；不证明业务精度或统计方法。'})

    def visit(value, ctes):
        if isinstance(value, list):
            for child in value:
                visit(child, ctes)
        elif isinstance(value, dict):
            if value.get('type') == 'SELECT_NODE':
                ctes = {**ctes, **{entry['key']: entry for entry in value['cte_map']['map']}}
                for child in value.values():
                    visit(child, ctes)
                inspect(value, ctes)
            else:
                for child in value.values():
                    visit(child, ctes)
    visit(root, {})
    return checked
