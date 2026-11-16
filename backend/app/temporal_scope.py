"""Check supported absolute date filters against requested/confirmed time scope."""
import re

from app.tools.join_validation import _parse

DATE = re.compile(r'^\d{4}-\d{2}(?:-\d{2})?(?:[ T].*)?$')
PERIOD = re.compile(r'\d{4}[-年/]\d{1,2}|[一二三四五六七八九十\d]+月|本周|上周|本年|去年|最近|过去|期间|日期|时间|\b(?:month|year|date|period|since|until)\b', re.I)
FIELD = re.compile(r'date|time|month|year|_at$|日期|时间|月份|期间', re.I)


def verify_temporal_scope(brief, question, *, sql=None, connection=None):
    quotes = [spec['request_quote'] for spec in brief.get('acceptance', []) if spec.get('request_quote')]
    text = '\n'.join(quotes) if quotes else question
    confirmed = [s['text'] for s in [*brief.get('scope_sources', []),*brief.get('continuation_sources',[])] if PERIOD.search(s['text'])
                 and not re.match(r'^(?:[一二三四五六七八九十\d]+月|\d{4}-\d{2})\s*(?:为|指|定义)', s['text'])]
    scope = '\n'.join([text, *confirmed])

    def check(value):
        if not isinstance(value, str) or not DATE.fullmatch(value):
            return
        if not PERIOD.search(scope):
            raise ValueError('时间筛选没有本项用户原话或已确认期间依据；时间定义不能代替筛选要求，请移除未请求的日期条件。')
        if value.endswith('23:59:59') and not re.search(r'23[:：]59[:：]59', scope):
            raise ValueError('整天或整月不能用23:59:59截止，可能漏掉毫秒记录；请用下一天/下一月起点作为排他上界（lt / <）。用户明确指定该时刻时才保留。')

    for stage in (brief.get('calculation_plan') or {}).get('stages', []):
        for predicate in stage.get('filters', []):
            if predicate.get('cast') in {'date','timestamp','try_date','try_timestamp'} or FIELD.search(predicate.get('field','')):
                check(predicate.get('value'))
    if sql is None:
        return
    # shortcut: ISO literals on date-named/cast fields in WHERE/HAVING; relative date functions remain outside this check.
    def temporal(value):
        if isinstance(value, dict):
            return (value.get('cast_type', {}).get('id') in {'DATE','TIMESTAMP','TIMESTAMP_TZ'}
                    or value.get('class')=='COLUMN_REF' and any(FIELD.search(name) for name in value['column_names'])
                    or any(temporal(child) for child in value.values()))
        return isinstance(value, list) and any(temporal(child) for child in value)

    def literals(value):
        if isinstance(value, dict):
            if value.get('class')=='CONSTANT':check(value.get('value',{}).get('value'))
            for child in value.values():literals(child)
        elif isinstance(value, list):
            for child in value:literals(child)

    def walk(value, filtering=False):
        if isinstance(value, dict):
            if filtering and value.get('class') in {'COMPARISON','BETWEEN'} and temporal(value):
                literals(value)
            for key, child in value.items():
                walk(child, filtering or key in {'where_clause', 'having'})
        elif isinstance(value, list):
            for child in value:
                walk(child, filtering)
    walk(_parse(connection, sql))
