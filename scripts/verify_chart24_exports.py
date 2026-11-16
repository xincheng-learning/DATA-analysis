"""Independent CSV parsing and actual PNG pixel geometry, using existing teacher Conda."""
import argparse
import ast
import csv
import json
import math
from pathlib import Path


def verify(directory,reference,output):
    import numpy as np
    from PIL import Image
    from scipy import ndimage
    if output.exists():raise ValueError('保留旧核对记录，请选择新输出。')
    teacher=json.loads(reference.read_text(encoding='utf-8'));checks=[]
    for case in teacher['cases']:
        with (directory/(case['id']+'-full.csv')).open(encoding='utf-8-sig',newline='') as stream:
            reader=csv.DictReader(stream);rows=list(reader);assert reader.fieldnames==list(case['rows'][0])
        assert len(rows)==len(case['rows'])
        for actual,expected in zip(rows,case['rows']):
            for key,value in expected.items():
                if value is None:assert actual[key]==''
                elif isinstance(value,(int,float)):assert math.isclose(float(actual[key]),value,rel_tol=1e-10,abs_tol=0)
                elif isinstance(value,dict):assert ast.literal_eval(actual[key])==value
                else:assert actual[key]==value
        with Image.open(directory/(case['id']+'-export.png')) as png:assert png.size==(2800,1700)
        checks.append({'id':case['id'],'csv_complete_and_exact':True,'png_size':[2800,1700]})
    # Check painted PNG components, rather than trusting nominal pre-render coordinates.
    rgb=np.asarray(Image.open(directory/'SM23-export.png').convert('RGB')).astype(np.int16)
    mask=(rgb[:,:,2]-rgb[:,:,0]>30)&(rgb[:,:,1]-rgb[:,:,0]>15)&(rgb[:,:,2]>80)
    labels,count=ndimage.label(mask);sizes=np.bincount(labels.ravel());components=[]
    for i,bounds in enumerate(ndimage.find_objects(labels),1):
        if sizes[i]<1000:continue
        yy,xx=bounds;components.append({'pixels':int(sizes[i]),'box':[xx.start,yy.start,xx.stop,yy.stop]})
    components.sort(key=lambda r:r['pixels'])
    error='';passed=len(components)==3
    if not passed:error=f'期望3个不重叠圆，实际只有{len(components)}个独立着色区域；可能重叠。'
    if passed:
        for item,expected_area in zip(components,[1,4,9]):
            left,top,right,bottom=item['box'];ratio=item['pixels']/components[0]['pixels']
            if not math.isclose(ratio,expected_area,rel_tol=.025):passed=False;error='实际PNG圆面积比例不符合1:4:9。'
            if not (180<=left<right<=2660 and 220<=top<bottom<=1520):passed=False;error='圆超出实际PNG绘图区，存在裁切。'
            if not math.isclose(right-left,bottom-top,rel_tol=.02):passed=False;error='导出圆形被非等比缩放。'
    result={'passed':passed,'scope':'actual application HTTP CSV bodies and UI-exported PNG pixels; independent teacher values',
        'csv_cases':checks,'packed_png':{'passed':passed,'components':components,'reason':error}}
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':passed,'csv_cases':len(checks),'packed_png':result['packed_png']},ensure_ascii=False,indent=2))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--directory',type=Path,required=True);parser.add_argument('--reference',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    a=parser.parse_args();result=verify(a.directory,a.reference,a.output)
    if not result['passed']:raise SystemExit(1)
