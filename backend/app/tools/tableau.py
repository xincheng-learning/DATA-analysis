"""Additional Tableau forms on the existing Plotly/source receipt boundary."""
import json
import math
from datetime import datetime
from decimal import Decimal
from numbers import Real


def build_tableau_chart(spec,rows):
    family=spec.chart_type
    required={'gantt':[spec.start_column,spec.end_column], 'bullet':[spec.y_column,spec.target_column],
        'packed_bubble':[spec.y_column], 'dual_line':[spec.y_column,spec.secondary_column],
        'combo':[spec.y_column,spec.secondary_column], 'symbol_map':[spec.longitude_column,spec.latitude_column],
        'filled_map':[spec.y_column,spec.geometry_column]}[family]
    if any(not name for name in required) or spec.series_column or spec.y_columns or spec.row_limit:
        raise ValueError(f'{family} 需要明确的来源字段；不能猜目标、日期或地理信息，也不支持混合排名/系列设置。')
    columns=list(dict.fromkeys([spec.x_column,*required,*[c for c in (spec.size_column,spec.color_column) if c]]))
    measures=[c for c in required if c not in (spec.start_column,spec.end_column,spec.geometry_column)]
    measures=list(dict.fromkeys([*measures,*[c for c in (spec.size_column,spec.color_column) if c]]))
    for column in measures:
        if any(v is not None and (isinstance(v,bool) or not isinstance(v,(Real,Decimal)) or not math.isfinite(v)) for v in (r[column] for r in rows)):
            raise ValueError(f'{column} 必须是有限数值或空值。')
    labels=[r[spec.x_column] for r in rows]
    if family not in {'symbol_map'} and len(set(labels))!=len(labels):
        raise ValueError('图表类别/地区标识重复，请先按实际粒度处理，不能覆盖或重复叠加。')
    meta={'family':family,'source_columns':columns,'measure_columns':measures,'x_column':spec.x_column,
        'y_column':spec.y_column,'value_scale':spec.value_scale}
    meta.update({name:getattr(spec,name) for name in ('start_column','end_column','target_column','geometry_column','longitude_column','latitude_column') if getattr(spec,name)})
    custom=[[r[c] for c in columns] for r in rows]
    layout={'title':spec.title,'xaxis':{'title':spec.x_label},'yaxis':{'title':spec.y_label},'showlegend':False}
    if spec.size_column and any(r[spec.size_column] is None or r[spec.size_column]<0 for r in rows):
        raise ValueError('面积字段必须非负且已知，未知不能补零。')
    if family=='gantt':
        durations=[]
        for row in rows:
            try:
                start=datetime.fromisoformat(str(row[spec.start_column]).replace('Z','+00:00'))
                end=datetime.fromisoformat(str(row[spec.end_column]).replace('Z','+00:00'))
                duration=(end-start).total_seconds()*1000
            except (ValueError,TypeError):
                raise ValueError('开始/结束必须为可比较的ISO日期或时间，时区口径需一致。') from None
            if duration<0:raise ValueError('结束时间早于开始时间，未生成甘特图。')
            durations.append(duration)
        trace={'type':'bar','orientation':'h','y':labels,'x':durations,'base':[str(r[spec.start_column]) for r in rows],
            'customdata':custom,'meta':meta,'hovertemplate':'%{y}<br>开始：%{base}<br>结束：%{customdata[2]}<extra></extra>'}
        layout.update(xaxis={'type':'date','title':'日期 / 时间','tickformat':'%m-%d'},yaxis={'title':spec.x_label,'autorange':'reversed'},
            meta={'chart_note':'条形跨度为实际结束减开始；同日任务跨度为零，原始起止仍可查。'})
        return {'data':[trace],'layout':layout}
    if family=='bullet':
        if any(r[c] is None or r[c]<0 for r in rows for c in required):raise ValueError('子弹图实际值和明确目标必须为已知非负值。')
        actual=[r[spec.y_column] for r in rows];targets=[r[spec.target_column] for r in rows]
        traces=[{'type':'bar','orientation':'h','x':actual,'y':labels,'name':'实际','width':.38,'customdata':custom,'meta':meta},
            {'type':'scatter','mode':'markers','x':targets,'y':labels,'name':'目标','marker':{'symbol':'line-ns','size':26,'line':{'width':3}},
                'customdata':custom,'meta':{**meta,'y_column':spec.target_column,'value_role':'target'}}]
        layout.update(xaxis={'title':spec.y_label,'rangemode':'tozero'},yaxis={'title':spec.x_label},showlegend=True,
            meta={'chart_note':'条形为实际值，竖线为提供的目标；未猜测业务阈值。'})
        return {'data':traces,'layout':layout}
    if family in {'dual_line','combo'}:
        traces=[]
        for i,(column,label,scale) in enumerate([(spec.y_column,spec.y_label,spec.value_scale),(spec.secondary_column,spec.secondary_label or spec.secondary_column,spec.secondary_scale)]):
            trace={'type':'bar' if family=='combo' and i==0 else 'scatter','x':labels,'y':[r[column] for r in rows],
                'name':label,'yaxis':'y2' if i else 'y','meta':{**meta,'y_column':column,'metric_column':column,'value_scale':scale}}
            if trace['type']=='scatter':trace.update(mode='lines+markers',connectgaps=False)
            traces.append(trace)
        layout.update(yaxis2={'title':spec.secondary_label or spec.secondary_column,'overlaying':'y','side':'right','showgrid':False},
            showlegend=True,meta={'dual_axis':True,'chart_note':'左右轴独立尺度；分别按轴标签读取，不相加、不凭线条交叉推断关系。'})
        for key,scale in [('yaxis',spec.value_scale),('yaxis2',spec.secondary_scale)]:
            if scale=='ratio':layout[key]['tickformat']='.0%'
            elif scale=='percent':layout[key]['ticksuffix']='%'
        return {'data':traces,'layout':layout}
    if family=='packed_bubble':
        values=[r[spec.y_column] for r in rows]
        if any(v is None or v<0 for v in values):raise ValueError('紧密气泡需要已知非负面积，未知不能当零。')
        return {'data':[{'type':'scatter','mode':'markers+text','x':labels,'y':values,'text':labels,'customdata':custom,
            'meta':meta,'marker':{'size':values,'sizemode':'area'}}],
            'layout':{**layout,'meta':{'packed_bubble':True,'chart_note':'圆面积表示实际数值；位置仅用于紧密排列，无坐标含义。零面积不补造大小，原始值保留在结果表。'}}}
    marker={'size':12}
    if spec.size_column:
        values=[r[spec.size_column] for r in rows]
        marker.update(size=values,sizemode='area',sizeref=2*max(values)/2500 if max(values) else 1,sizemin=0)
        meta['size_column']=spec.size_column
    if spec.color_column:
        marker.update(color=[r[spec.color_column] for r in rows],colorscale='Blues',showscale=True,colorbar={'title':spec.color_column})
        meta['color_column']=spec.color_column
    if family=='symbol_map':
        lons=[r[spec.longitude_column] for r in rows];lats=[r[spec.latitude_column] for r in rows]
        if any(v is None or not -180<=v<=180 for v in lons) or any(v is None or not -85<=v<=85 for v in lats):
            raise ValueError('经纬度缺失或超出地图范围（经度±180，纬度±85），未猜测或丢弃位置。')
        traces=[{'type':'scattermap','mode':'markers','lon':lons,'lat':lats,'text':labels,'marker':marker,
            'customdata':custom,'meta':meta,'hovertemplate':'%{text}<br>经度：%{lon}<br>纬度：%{lat}<extra></extra>'}]
        for channel in (spec.size_column,spec.color_column):
            if channel:traces[0]['hovertemplate']=traces[0]['hovertemplate'].replace('<extra>',f'<br>{channel}：%{{customdata[{columns.index(channel)}]}}<extra>')
    else:
        features=[];points=[]
        for row,label in zip(rows,labels):
            raw=row[spec.geometry_column]
            try:geometry=json.loads(raw) if isinstance(raw,str) else raw
            except (ValueError,TypeError):raise ValueError('区域边界必须是实际GeoJSON几何，不能是URL或地名。') from None
            if not isinstance(geometry,dict) or geometry.get('type') not in {'Polygon','MultiPolygon'}:raise ValueError('填充地图需要Polygon或MultiPolygon边界。')
            polygons=[geometry.get('coordinates')] if geometry['type']=='Polygon' else geometry.get('coordinates')
            if not isinstance(polygons,list) or not polygons:raise ValueError('区域边界没有有效坐标。')
            for polygon in polygons:
                if not isinstance(polygon,list) or not polygon:raise ValueError('区域边界缺少闭合环。')
                for ring in polygon:
                    if not isinstance(ring,list) or len(ring)<4 or ring[0]!=ring[-1]:raise ValueError('区域边界必须是至少4点的闭合环。')
                    for point in ring:
                        if not isinstance(point,list) or len(point)<2 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in point[:2]) or not -180<=point[0]<=180 or not -85<=point[1]<=85:
                            raise ValueError('区域边界坐标无效或超出经纬度范围。')
                        points.append(point[:2])
            features.append({'type':'Feature','id':str(label),'properties':{},'geometry':geometry})
        lons=[p[0] for p in points];lats=[p[1] for p in points]
        traces=[{'type':'choroplethmap','geojson':{'type':'FeatureCollection','features':features},'locations':[str(v) for v in labels],
            'z':[r[spec.y_column] for r in rows],'customdata':custom,'meta':meta,'colorscale':'Blues','marker':{'line':{'width':2,'color':'#ffffff'}},
            'colorbar':{'title':spec.y_label},'hovertemplate':'%{location}<br>%{z}<extra></extra>'}]
    span=max(max(lons)-min(lons),(max(lats)-min(lats))*1.3,.01)
    layout={'title':spec.title,'map':{'style':{'version':8,'sources':{'local-empty':{'type':'geojson','data':{'type':'FeatureCollection','features':[]}}},'layers':[{'id':'background','type':'background','paint':{'background-color':'#f1f5f7'}}]},
        'center':{'lon':(max(lons)+min(lons))/2,'lat':(max(lats)+min(lats))/2},'zoom':max(0,min(14,math.log2(180/span)-.5))},
        'showlegend':False,'meta':{'geo_bounds':[min(lons),min(lats),max(lons),max(lats)],'chart_note':'仅绘制提供的坐标/边界；离线空白底图，没有在线地理补全。悬停查看地区与数值。'}}
    if family=='filled_map' and any(row[spec.y_column] is None for row in rows):
        layout['map']['layers']=[{'sourcetype':'geojson','source':traces[0]['geojson'],'type':'fill','color':'#aab8c2','below':'traces'}]
        layout['meta']['chart_note']+='灰色区域为未知值，不当零；具体值仍在完整结果中。'
    return {'data':traces,'layout':layout}
