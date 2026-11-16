"""Split CSV records under current upload limits, preserving all rows and multiline fields."""
import csv
import hashlib
import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT.parent / '数据分析/data/raw'
OUT = ROOT / 'output/olist-upload-parts-20261116'
FILES = {
    'orders': 'olist_orders_dataset.csv', 'order_items': 'olist_order_items_dataset.csv',
    'order_payments': 'olist_order_payments_dataset.csv', 'reviews': 'olist_order_reviews_dataset.csv',
    'customers': 'olist_customers_dataset.csv', 'products': 'olist_products_dataset.csv',
    'sellers': 'olist_sellers_dataset.csv', 'category_translation': 'product_category_name_translation.csv',
    'geolocation': 'olist_geolocation_dataset.csv',
}


def encoded(row):
    buffer = io.StringIO(newline='')
    csv.writer(buffer, lineterminator='\n').writerow(row)
    return buffer.getvalue().encode('utf-8')


def build():
    assert not OUT.exists(), 'Preserve existing upload parts; inspect their manifest instead of overwriting.'
    OUT.mkdir(parents=True)
    manifest = {'max_part_bytes': 9 * 1024 * 1024, 'max_part_rows': 150_000, 'tables': []}
    for table, filename in FILES.items():
        original = RAW / filename
        record = {'logical_table': table, 'original': filename, 'sha256': hashlib.sha256(original.read_bytes()).hexdigest(), 'parts': []}
        source_digest = hashlib.sha256()
        stream = None
        with original.open(encoding='utf-8-sig', newline='') as source:
            reader = csv.reader(source)
            header = next(reader)
            header_bytes = encoded(header)
            for row in reader:
                data = encoded(row)
                source_digest.update(data)
                if stream is None or part['rows'] >= manifest['max_part_rows'] or stream.tell() + len(data) > manifest['max_part_bytes']:
                    if stream is not None:
                        stream.close()
                    part = {'file': f'{table}_part{len(record["parts"]) + 1:03d}.csv', 'rows': 0}
                    record['parts'].append(part)
                    stream = (OUT / part['file']).open('wb')
                    stream.write(header_bytes)
                stream.write(data)
                part['rows'] += 1
        if stream is not None:
            stream.close()
        readback_digest = hashlib.sha256()
        for part in record['parts']:
            file = OUT / part['file']
            part['bytes'] = file.stat().st_size
            assert part['bytes'] <= manifest['max_part_bytes'] and part['rows'] <= manifest['max_part_rows']
            with file.open(encoding='utf-8', newline='') as source:
                reader = csv.reader(source)
                assert next(reader) == header
                count = 0
                for row in reader:
                    readback_digest.update(encoded(row))
                    count += 1
            assert count == part['rows']
        assert source_digest.digest() == readback_digest.digest(), 'Split changed record content or order.'
        record['rows'] = sum(part['rows'] for part in record['parts'])
        record['canonical_rows_sha256'] = source_digest.hexdigest()
        manifest['tables'].append(record)
    (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'logical_tables': len(manifest['tables']), 'parts': sum(len(t['parts']) for t in manifest['tables']),
                      'rows': sum(t['rows'] for t in manifest['tables']), 'record_order_content_verified': True}, ensure_ascii=False))


if __name__ == '__main__':
    build()
