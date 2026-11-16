"""Join explanatory labels to measured source fields, never model-generated counts."""
import json

from app.execution import publish_answer


def guide_table(table, explanation, descriptions):
    detail = next((item for item in explanation.tables if item.table_name == table['table_name']), None)
    meanings = {field.column: field for field in detail.fields} if detail else {}
    fields = []
    for column in table['columns']:
        meaning = meanings.get(column)
        count = table['null_counts'][column]
        sample = next((row[column] for row in table['preview'] if row.get(column) is not None), None)
        impact = explanation.impacts.get(f"{table['table_name']}.{column}", '') if count else ''
        fields.append({'column':column, 'label':meaning.label if meaning else column,
            'meaning':meaning.meaning if meaning else '字段业务含义尚需结合数据字典确认。',
            'dtype':table['dtypes'][column], 'missing':count,
            'missing_percent':count/table['rows']*100 if table['rows'] else None,
            'sample':sample, 'impact':impact})
    return {'table_name':table['table_name'], 'title':detail.title if detail else table['table_name'],
        'description':detail.description if detail else descriptions.get(table['table_name'],'已导入的数据表。'),
        'rows':table['rows'], 'fields':fields, 'preview':table['preview'][:3],
        'source':table.get('source',{}), 'filename':table['filename']}


def guide_markdown(table):
    def cell(value):
        text = json.dumps(value,ensure_ascii=False) if isinstance(value,(list,dict)) else str(value) if value is not None else '样例中为空'
        return text.replace('|','\\|').replace('\n',' ')
    lines=[f"### {table['title']} · `{table['table_name']}`", table['description'],
        f"实际导入 {table['rows']:,} 行 · {len(table['fields'])} 个字段", '\n'.join([
        '| 原始字段 | 中文含义与用途 | 类型 | 真实样例 | 缺失情况 | 分析提示 |',
        '| --- | --- | --- | --- | --- | --- |',
        *[f"| `{field['column']}` | {cell(field['label'])}：{cell(field['meaning'])} | {cell(field['dtype'])} | {cell(field['sample'])} | "
          + (f"{field['missing']:,} 行（{field['missing_percent']:.2f}%）" if field['missing_percent'] is not None else '无记录，比例不适用')
          + f" | {cell(field['impact']) if field['impact'] else '保留原始记录；空值不代填。' if field['missing'] else '未检测到NULL空值。'} |" for field in table['fields']]])]
    return '\n\n'.join(lines)


def build_guide(tables, explanation, descriptions):
    guide=[guide_table(table,explanation,descriptions) for table in tables]
    answer=f'## 当前导入了 {len(tables)} 张表\n\n按表阅读：每一行字段说明都对应原表中的同名列；缺失数量按整张导入表计算。\n\n'
    answer+='\n\n'.join(guide_markdown(table) for table in guide)
    answer+='\n\n空值仅表示未记录；不能单凭空值判定业务事件未发生、多列影响同一批记录或缺失原因。字段解释不能替代主键和关联关系核验。'
    if explanation.next_steps:
        answer+='\n\n## 后续建议\n\n'+'\n'.join('- '+text for text in explanation.next_steps)
    return {'answer':answer,'data_dictionary':guide,'data_dictionary_next_steps':explanation.next_steps}


def stream_guide(tables, descriptions):
    published=set()
    def receive(explanation):
        if not hasattr(explanation,'tables'):
            return
        actual={table['table_name']:table for table in tables}
        for item in explanation.tables[:-1]:
            if item.table_name in actual and item.table_name not in published:
                published.add(item.table_name)
                publish_answer('\n\n'+guide_markdown(guide_table(actual[item.table_name],explanation,descriptions)),append=True)
    return receive
