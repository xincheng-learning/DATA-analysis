"""Independent chart-content checks; no product calculations or rendering imports."""
from copy import deepcopy
import math


def same(actual, expected):
    if isinstance(expected, list):
        assert isinstance(actual, list) and len(actual)==len(expected), 'array length differs'
        for a,b in zip(actual,expected):same(a,b)
    elif isinstance(expected,(int,float)) and not isinstance(expected,bool):
        assert isinstance(actual,(int,float)) and not isinstance(actual,bool) and math.isfinite(actual)
        assert math.isclose(actual,expected,rel_tol=1e-10,abs_tol=0), f'value differs: {actual} != {expected}'
    else:assert actual==expected, f'value differs: {actual} != {expected}'


def check(case, figure):
    rows=case['rows'];ident=case['id'];kind=case['kind'];data=figure['data'];layout=figure.get('layout',{})
    assert data, 'missing figure'
    values=lambda name:[r[name] for r in rows]
    if kind=='text_table':
        assert data[0]['type']=='table';columns=list(rows[0]);same(data[0]['header']['values'],columns)
        same(data[0]['cells']['values'],[values(c) for c in columns])
    elif kind in {'heatmap_marks','highlight_table'}:
        t=data[0]
        if kind=='heatmap_marks':
            assert t['type']=='scatter' and 'markers' in t['mode'];same(t['x'],values('month'));same(t['y'],values('region'))
            same(t['marker']['size'],values('orders'));same(t['marker']['color'],values('amount_cny'));assert t['marker']['sizemode']=='area'
        else:
            assert t['type']=='heatmap';same(t['x'],['一月','二月','三月']);same(t['y'],['华东','华南']);same(t['z'],[[0,30,None],[10,60,90]])
    elif kind=='symbol_map':
        t=data[0];assert t['type'] in ('scattermap','scattergeo');same(t['lon'],values('longitude'));same(t['lat'],values('latitude'));same(t['text'],values('place'));same(t['marker']['size'],values('amount'));assert t['marker']['sizemode']=='area'
    elif kind=='filled_map':
        t=data[0];assert t['type'] in ('choroplethmap','choropleth');same(t['locations'],values('region'));same(t['z'],values('amount'))
        features=t['geojson']['features'];assert len(features)==len(rows)
        for r in rows:
            feature=next(f for f in features if f.get('id')==r['region'] or r['region'] in f.get('properties',{}).values())
            assert feature['geometry']==r['geometry']
    elif kind=='pie':
        t=data[0];assert t['type']=='pie';same(t['labels'],values('category'));same(t['values'],values('amount_cny'));assert sum(t['values'])==100
    elif kind=='bar':
        if ident=='SM07':
            assert data[0]['type']=='bar' and data[0]['orientation']=='h';same(data[0]['y'],values('category'));same(data[0]['x'],values('amount_cny'))
        else:
            assert len(data)==2 and layout['barmode']==('stack' if ident=='SM08' else 'group')
            for t,col in zip(data,['amount_cny','other_cny']):assert t['type']=='bar';same(t['x'],values('category'));same(t['y'],values(col))
    elif kind in {'circle','line','area','scatter'}:
        column='month' if 'month' in rows[0] else 'category'
        measures=['amount_cny','other_cny'] if kind=='area' or ident=='SM11' else ['y_value'] if kind=='scatter' else ['amount_cny']
        assert len(data)==len(measures)
        for t,col in zip(data,measures):
            assert t['type']=='scatter';same(t['x'],values('x_value' if kind=='scatter' else column));same(t['y'],values(col))
            if kind in ('circle','scatter'):assert t['mode']=='markers'
            else:assert 'lines' in t['mode'] and not t.get('connectgaps',False)
            if kind=='area':assert t.get('stackgroup')
        if ident in ('SM13','SM16'):assert layout['xaxis']['type']=='category'
        if ident=='SM11':assert layout.get('scattermode')=='group'
    elif kind in {'dual_line','combo'}:
        assert len(data)==2
        for t,col in zip(data,['amount_cny','conversion_ratio']):same(t['x'],values('month'));same(t['y'],values(col))
        assert data[1]['yaxis']=='y2' and 'lines' in data[1]['mode'] and not data[1].get('connectgaps',False)
        assert data[0]['type']==('bar' if kind=='combo' else 'scatter')
        assert layout['yaxis2'].get('side')=='right' and '%' in layout['yaxis2'].get('tickformat','')
    elif kind=='histogram':
        t=data[0];assert t['type']=='histogram';same(t['x'],values('amount_cny'));assert t.get('histnorm') in (None,'')
    elif kind=='box':
        assert len(data)==2 and layout['meta']['box_focus']
        for t in data:assert t['type']=='box' and t['quartilemethod']=='linear';same(t['x'],values('group'));same(t['y'],values('amount_cny'))
        assert layout['yaxis']['range'][1]-layout['yaxis']['range'][0]<=12 and layout['yaxis2']['range'][1]>=100
        expected=[('A',6,2,3.5,5,1,5,[100]),('B',4,3,5,7,2,8,[])]
        for actual,want in zip(layout['meta']['box_statistics'],expected):
            same([actual[k] for k in ('group','n','q1','median','q3','whisker_low','whisker_high','outliers')],list(want))
    elif kind=='gantt':
        t=data[0];assert t['type']=='bar' and t['orientation']=='h';same(t['y'],values('task'));same(t['base'],values('start'));same(t['x'],[2*86400000,5*86400000]);assert layout['xaxis']['type']=='date'
    elif kind=='bullet':
        assert len(data)==2;assert data[0]['type']=='bar' and data[0]['orientation']=='h';same(data[0]['x'],values('actual'));same(data[0]['y'],values('group'));same(data[1]['x'],values('target'));same(data[1]['y'],values('group'))
    elif kind=='packed_bubble':
        t=data[0];assert t['meta']['family']=='packed_bubble';same(t['text'],values('group'));same(t['x'],values('group'));same(t['y'],values('amount'));same(t['marker']['size'],values('amount'));assert t['marker']['sizemode']=='area'
    elif kind=='treemap':
        t=data[0];assert t['type']=='treemap' and t['branchvalues']=='total' and len(set(t['ids']))==7
        expected={'全部':100,'东区':50,'东区 › 甲':20,'东区 › 乙':30,'西区':50,'西区 › 甲':10,'西区 › 乙':40}
        assert {p[0]:float(p[1]) for p in t['customdata']}==expected
        for ident,value in zip(t['ids'],t['values']):
            kids=[v for p,v in zip(t['parents'],t['values']) if p==ident]
            if kids:same(sum(kids),value)
        same(t['values'][0],100)
    else:raise AssertionError('unrecognized reference family')
    return True


def corrupt(figure):
    """Change one plotted number without changing source receipts or metadata."""
    altered=deepcopy(figure)
    def change(value):
        if not isinstance(value,list):return False
        for i,number in enumerate(value):
            if isinstance(number,(int,float)) and not isinstance(number,bool):value[i]=number+12345;return True
            if change(number):return True
        return False
    for trace in altered['data']:
        for key in ('values','lon','z','x','y'):
            if change(trace.get(key)):return altered
        if change(trace.get('cells',{}).get('values')):return altered
        if change(trace.get('marker',{}).get('size')):return altered
    raise ValueError('No plotted number to mutate')
