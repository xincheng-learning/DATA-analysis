"""Build/check original semantic gold; no secrets, paid API or production data."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.reliability_dataset import VERSION, build_cases, profiles, verify_references

GOLD = ROOT / 'evals/reliability_v1'


def check():
    manifest = json.loads((GOLD / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['source_sha256'] == hashlib.sha256((ROOT / 'evals/reliability_dataset.py').read_bytes()).hexdigest()
    for name, expected in manifest['files'].items():
        file = (GOLD / name).resolve()
        assert file.is_relative_to(GOLD.resolve()), name
        assert hashlib.sha256(file.read_bytes()).hexdigest() == expected, name
    checked = verify_references()
    assert len(checked) == manifest['cases'] == 36
    answers = json.loads((GOLD / 'answers.json').read_text(encoding='utf-8'))['answers']
    for case in build_cases():
        for actual, turn in zip(answers[case['id']], case['turns'], strict=True):
            assert actual == {'columns': turn['expected_columns'], 'rows': turn['expected_rows'],
                              'comparison': turn['comparison'], 'sql': turn['oracle_sql']}, case['id']
    return manifest


def build():
    if (GOLD / 'manifest.json').exists():
        raise ValueError('补充集已冻结；日常只用--check。标准修改须新版本并保留旧记录。')
    checked = verify_references()
    files = []
    def write(name, content):
        file = GOLD / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding='utf-8', newline='\n')
        files.append(name)
    def document(value):
        return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    public, answers, mutants = [], {}, []
    for case in build_cases():
        item = {k: v for k, v in case.items() if k not in {'turns', 'wrong_sql'}}
        item['turns'] = [{k: v for k, v in turn.items() if k in {'question', 'allowed_routes'}} for turn in case['turns']]
        public.append(item)
        answers[case['id']] = [{'columns': t['expected_columns'], 'rows': t['expected_rows'],
            'comparison': t['comparison'], 'sql': t['oracle_sql']} for t in case['turns']]
        mutants.append({'case': case['id'], 'sql': case['wrong_sql'], 'expected': 'fail', 'failure': case['family']})
    write('cases.json', document({'version': VERSION, 'cases': public}))
    write('answers.json', document({'version': VERSION, 'answers': answers}))
    write('adversarial.json', document({'version': VERSION, 'mutants': mutants}))
    write('fixtures.json', document(profiles()))
    write('reference-check.json', document({'real_model_called': False, 'checks': checked}))
    for profile, tables in profiles().items():
        for name, rows in tables.items():
            file = GOLD / 'data' / profile / (name + '.csv')
            file.parent.mkdir(parents=True, exist_ok=True)
            with file.open('w', encoding='utf-8', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
                writer.writeheader()
                writer.writerows(rows)
            files.append(file.relative_to(GOLD).as_posix())
    manifest = {'version': VERSION, 'cases': len(public), 'families': 12, 'variants_per_family': 3,
        'split': 'development only; not a blind benchmark', 'original_synthetic_data': True,
        'source_sha256': hashlib.sha256((ROOT / 'evals/reliability_dataset.py').read_bytes()).hexdigest(),
        'files': {name: hashlib.sha256((GOLD / name).read_bytes()).hexdigest() for name in files}}
    write('manifest.json', document(manifest))
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    result = check() if args.check else build()
    print(json.dumps({k: v for k, v in result.items() if k not in {'files', 'source_sha256'}}, ensure_ascii=False, indent=2))
