"""Offline independent chart checks plus a plotted-wrong-number negative for each."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from evals.chart24_scoring import check, corrupt


def score(directory,reference,output):
    if output.exists():raise ValueError('保留旧评分，请选择新的输出路径。')
    teacher=json.loads(reference.read_text(encoding='utf-8'));outcomes=[]
    for case in teacher['cases']:
        source=directory/'cases'/(case['id']+'.json')
        result={'id':case['id'],'name':case['name'],'numeric':'fail','mutation_rejected':False}
        try:
            record=json.loads(source.read_text(encoding='utf-8'));figure=record['chart_spec'];check(case,figure)
            result['numeric']='pass'
            try:check(case,corrupt(figure))
            except (AssertionError,KeyError,ValueError,StopIteration):result['mutation_rejected']=True
        except (AssertionError,KeyError,ValueError,StopIteration,FileNotFoundError) as exc:result['reason']=str(exc) or type(exc).__name__
        outcomes.append(result)
    summary={'scope':'fixed-input chart content and semantics; not visual, browser or complete SQL-agent certification',
        'scorer_sha256':hashlib.sha256((ROOT/'evals/chart24_scoring.py').read_bytes()).hexdigest(),
        'reference_sha256':hashlib.sha256(reference.read_bytes()).hexdigest(),
        'cases':len(outcomes),'numeric_passed':sum(r['numeric']=='pass' for r in outcomes),
        'wrong_number_rejected':sum(r['mutation_rejected'] for r in outcomes),'outcomes':outcomes}
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2));return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run',type=Path,required=True);parser.add_argument('--reference',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();s=score(args.run,args.reference,args.output)
    if s['cases']!=24 or s['numeric_passed']!=24 or s['wrong_number_rejected']!=24:raise SystemExit(1)
