"""Small deterministic checks with explicit scope; no model-based plausibility score."""
from collections import Counter
from copy import deepcopy

import duckdb

from app.tools.join_validation import _parse, _sql, _expressions


def aggregate_coverage(con, sql):
    checked=[]
    root=_parse(con,sql)
    def visit(value,ctes):
        if isinstance(value,list):
            for child in value:visit(child,ctes)
        elif isinstance(value,dict):
            if value.get('type')!='SELECT_NODE':
                for child in value.values():visit(child,ctes)
                return
            ctes={**ctes,**{entry['key']:entry for entry in value['cte_map']['map']}}
            for child in value.values():visit(child,ctes)
            expressions=[item for item in _expressions(value['select_list']) if item.get('class')=='FUNCTION'
                and item.get('function_name','').lower() in {'sum','avg'} and len(item.get('children',[]))==1
                and item['children'][0].get('class')=='COLUMN_REF']
            if not expressions:return
            if value.get('having') or value.get('qualify'):
                checked.append({'id':'aggregate_coverage','status':'not_checked',
                    'detail':'此查询含 HAVING/QUALIFY，尚未独立核对筛选后保留组的输入覆盖；未据此认定当前结果缺失或完整。'})
                return
            probe=deepcopy(value)
            probe.update(cte_map={'map':list(ctes.values())},modifiers=[],group_expressions=[],group_sets=[],having=None,qualify=None,select_list=[])
            for expression in expressions:
                pair=_parse(con,'SELECT count(*),count(x) FROM _placeholder')['select_list']
                pair[1]['children']=[deepcopy(expression['children'][0])]
                for item in pair:item['filter']=deepcopy(expression.get('filter'))
                probe['select_list'].extend(pair)
            try:
                counts=con.execute(_sql(con,probe)).fetchone()
            except duckdb.BinderException:
                checked.append({'id':'aggregate_coverage','status':'not_checked','detail':'相关或派生聚合未能独立核对输入覆盖，不据此确认完整金额。'})
                return
            for index,expression in enumerate(expressions):
                total,known=counts[index*2:index*2+2]
                missing=total-known
                metric=expression['function_name'].upper()+'('+'.'.join(expression['children'][0]['column_names'])+')'
                checked.append({'id':'aggregate_coverage','status':'warning' if missing else 'passed','metric':metric,'input_rows':total,'missing_rows':missing,
                    'detail':f'{metric} 的 {total} 行输入中有 {missing} 行空值；'+('数值仅覆盖已知值，不能当完整总额或全量平均。' if missing else '该聚合输入未发现空值；不证明业务筛选口径已确认。')})
    visit(root,{})
    return checked


def verify_chart(chart,rows,row_count):
    plotted=0
    for trace in chart['data']:
        meta=trace.get('meta',{})
        x,y=meta.get('x_column'),meta.get('y_column')
        source=[row for row in rows if not meta.get('series_column') or row[meta['series_column']]==meta['series_value']]
        kind=trace['type']
        family=meta.get('family')
        if family in {'gantt','bullet','symbol_map','filled_map'}:
            if trace.get('customdata')!=[[row[c] for c in meta['source_columns']] for row in source]:
                raise ValueError('图表完整来源记录不匹配。')
            if family=='gantt':
                from datetime import datetime
                durations=[(datetime.fromisoformat(str(r[meta['end_column']]).replace('Z','+00:00'))-datetime.fromisoformat(str(r[meta['start_column']]).replace('Z','+00:00'))).total_seconds()*1000 for r in source]
                valid=trace['y']==[r[x] for r in source] and trace['x']==durations and trace['base']==[str(r[meta['start_column']]) for r in source]
            elif family=='bullet':
                valid=trace['y']==[r[x] for r in source] and trace['x']==[r[y] for r in source]
            elif family=='symbol_map':
                valid=trace['lon']==[r[meta['longitude_column']] for r in source] and trace['lat']==[r[meta['latitude_column']] for r in source] and trace['text']==[r[x] for r in source]
            else:
                import json
                geometries=[json.loads(r[meta['geometry_column']]) if isinstance(r[meta['geometry_column']],str) else r[meta['geometry_column']] for r in source]
                valid=trace['locations']==[str(r[x]) for r in source] and trace['z']==[r[y] for r in source] and [f['geometry'] for f in trace['geojson']['features']]==geometries and [f['id'] for f in trace['geojson']['features']]==trace['locations']
            if not valid:raise ValueError('日期、目标或地理图形数值与来源不匹配。')
            for channel in ('size','color'):
                if meta.get(channel+'_column') and trace['marker'][channel]!=[r[meta[channel+'_column']] for r in source]:raise ValueError('地图面积/颜色与来源不匹配。')
            plotted+=len(source);continue
        if kind=='table':
            from app.agent.nodes import _sanitize_records
            columns=meta['source_columns'];display=_sanitize_records([{name:row[name] for name in columns} for row in source])
            if trace['header']['values']!=columns or trace['cells']['values']!=[[row[name] for row in display] for name in columns]:
                raise ValueError('表图字段或数值不符合查询来源。')
            plotted+=len(source);continue
        if kind=='heatmap':
            cells={(row[x],row[y]):row[meta['metric_column']] for row in source}
            for j,label_y in enumerate(trace['y']):
                for i,label_x in enumerate(trace['x']):
                    if trace['z'][j][i]!=cells.get((label_x,label_y)):
                        raise ValueError('图表类别与数值配对不符合查询来源。')
            plotted+=len(cells)
            continue
        if kind=='histogram' and not y:
            expected=Counter(row[x] for row in source);actual=Counter(trace['x'])
        else:
            expected=Counter((row[x],row[y]) for row in source)
            if kind=='pie':pairs=zip(trace['labels'],trace['values'])
            elif kind=='funnel' or kind=='bar' and trace.get('orientation')=='h':pairs=zip(trace['y'],trace['x'])
            else:pairs=zip(trace['x'],trace['y'])
            actual=Counter(pairs)
            if kind=='pie':lengths=[len(trace['labels']),len(trace['values'])]
            else:lengths=[len(trace['x']),len(trace['y'])]
            if lengths[0]!=lengths[1]:raise ValueError('图表类别与数值数量不等，无法核对配对。')
        if actual-expected or kind in {'box','histogram'} and actual!=expected:
            raise ValueError('图表类别与数值配对不符合查询来源，或重复绘制了来源中不存在的点。')
        if family=='packed_bubble' and trace['marker']['size']!=[r[y] for r in source]:raise ValueError('气泡面积不符合查询来源。')
        for channel in ('size','color'):
            column=meta.get(channel+'_column')
            if column and trace.get('marker',{}).get(channel)!=[row[column] for row in source]:
                raise ValueError('图表面积/颜色数值不符合查询来源。')
        plotted+=sum(actual.values())
    return {'id':'chart_source','status':'passed','detail':'逐点类别与数值配对来自查询结果；排序和排名截取保留原值。',
            'source_rows':len(rows),'query_rows':row_count,'plotted_points':plotted,
            'scope':f'仅核对传入绘图的 {len(rows)} 行，查询共 {row_count} 行；不证明图外总体或业务口径。'}
