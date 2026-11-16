"""Bind supported explicit USER field-product formulas to output lineage."""
import re

from app.calculation_contract import CalculationMismatch


class UserFormulaMismatch(CalculationMismatch):
    check_id = 'user_formula'
    retryable = False


def explicit_products(text):
    field_name = r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?'
    for match in re.finditer(rf'({field_name})\s*[×*]\s*({field_name})', text):
        label = re.search(r'([\w\u4e00-\u9fff]+?)\s*(?:改为|调整为|定义为|为|等于|[=＝])\s*(?:sum\s*\()?\s*$', text[:match.start()], re.I)
        aggregation = re.match(r'\s*[)）]?\s*(?:合计|之和|求和|汇总)', text[match.end():])
        if label and (aggregation or re.search(r'sum\s*\(\s*$', text[:match.start()], re.I)):
            end = match.end() + (aggregation.end() if aggregation else 0)
            yield label[1], match[1], match[2], text[label.start():end]


def verify_user_formulas(dataset, brief, plan, question):
    # shortcut: explicit labelled two-field products only; other business formulas stay unverified.
    sources = [s['text'] for s in [*brief.get('scope_sources', []), *brief.get('continuation_sources', [])]]
    fields = [(f.table_name, c) for f in dataset.files for c in f.columns]
    stages = {s.name: s for s in plan.stages}

    def qualified(name, owners=None):
        candidates = [(t, c) for t, c in fields if (owners is None or t in owners) and name in {c, t+'.'+c}]
        return candidates[0][0]+'.'+candidates[0][1] if len(candidates) == 1 else name

    def resolve(stage, name):
        owners = [stage.source, *(j.table for j in stage.joins)]
        for owner in owners:
            if owner in stages:
                match = next((x for x in stages[owner].select if name in {x.name, owner+'.'+x.name}), None)
                if match:
                    return expression(stages[owner], match)
        raw = qualified(name, owners)
        if raw not in {t+'.'+c for t,c in fields}:
            alias = next((x for x in stage.select if x.name==name),None)
            if alias:return expression(stage,alias)
        return raw

    def product(a, b):
        return ('multiply', *sorted([a, b], key=str))

    def expression(stage, item):
        if item.op == 'column':
            return resolve(stage, item.field)
        if item.op=='zero_if_unmatched':
            return resolve(stage,item.numerator)
        if item.op in {'sum','sum_if'}:
            value=resolve(stage,item.field)
            return value if isinstance(value,tuple) and value[0]=='sum' else ('sum',value)
        if item.op == 'multiply':
            return product(resolve(stage, item.numerator), resolve(stage, item.denominator))
        if item.op == 'sum_product':
            return ('sum', product(resolve(stage, item.numerator), resolve(stage, item.denominator)))
        return (item.op,)

    formulas = {}
    for text in [*sources, question]:
        for label, a, b, _ in explicit_products(text):
            left, right = qualified(a), qualified(b)
            if left in {t+'.'+c for t, c in fields} and right in {t+'.'+c for t, c in fields}:
                formulas[label] = ('sum', product(left, right))
    checks = []
    for label, expected in formulas.items():
        for spec in brief.get('acceptance', []):
            columns = spec.get('result_columns', [])
            targets = [c for c in columns if label == c]
            heading = re.split(r'[（(:：]', spec.get('requirement', ''))[0]
            if not targets and len(columns) == 1 and re.search(r'(?:^|\s|[年月日]|商品|总)'+re.escape(label)+r'$', heading):
                targets = columns
            if not targets and len(columns)>1 and re.search(r'(?:^|\s|[年月日]|商品|总)'+re.escape(label)+r'(?=$|[、，,和及与])',heading):
                for column in columns:
                    owner=next((s for s in reversed(plan.stages) if any(x.name==column for x in s.select)),None)
                    if owner and expression(owner,next(x for x in owner.select if x.name==column))==expected:
                        targets.append(column)
                if not targets:
                    raise UserFormulaMismatch(f'本项要求包含用户公式“{label}”，但没有对应输出遵守该公式；请修正声明计划，不能只改SQL。')
            for target in targets:
                stage = next((s for s in reversed(plan.stages) if any(x.name == target for x in s.select)), None)
                if stage is None:
                    continue  # The regular output-column check reports the missing target.
                item = next(x for x in stage.select if x.name == target)
                if expression(stage, item) != expected:
                    raise UserFormulaMismatch(f'用户已确认公式“{label}”与输出“{target}”的声明计算不一致；不能只改SQL迎合该计划，请修正计划。')
                checks.append({'id': 'user_formula', 'status': 'passed', 'requirement_ids': [spec['id']],
                    'columns': [target], 'detail': f'输出“{target}”的计算血缘遵守用户明确字段乘法公式“{label}”；仅覆盖此公式，不认证其他业务口径。'})
    return checks
