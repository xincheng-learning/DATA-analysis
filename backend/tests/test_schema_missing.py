import io

from app.tools.dataset_manager import SessionDataset
from app.tools.ingest import validate_and_convert


def test_schema_missing_counts_are_real_and_cached(tmp_path):
    source = io.BytesIO(b"record_id,quantity,unit_price\nA,1,10\nB,2,\nC,-1,5\n")
    ingested = validate_and_convert("sales.csv", source, tmp_path)
    dataset = SessionDataset(session_id="null-schema", parquet_dir=tmp_path)
    dataset.add_file(ingested)
    try:
        schema = dataset.schema_summary()
        price_line = next(line for line in schema.splitlines() if "unit_price:" in line)
        assert "missing: 1" in price_line
        assert dataset.preview()[0]["null_counts"]["unit_price"] == 1
        assert dataset.schema_summary() is schema
    finally:
        dataset.close()


def test_sensitive_column_samples_are_not_sent_to_model(tmp_path):
    source = io.BytesIO('email,phone,name,category\nsecret@example.com,13800138000,张三,books\n'.encode())
    dataset = SessionDataset('privacy-schema', parquet_dir=tmp_path)
    dataset.add_file(validate_and_convert('people.csv', source, tmp_path))
    schema = dataset.schema_summary()
    assert 'secret@example.com' not in schema and '13800138000' not in schema and '张三' not in schema
    assert 'email: VARCHAR' in schema and 'books' in schema
    dataset.close()
