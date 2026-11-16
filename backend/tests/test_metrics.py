from app import metrics


def test_definitions_distinguish_cash_timing_from_order_timing_and_missing_data():
    catalog = {item['id']: item for item in metrics.reference_catalog()}
    assert catalog['net_cash_receipts']['date_basis'] != catalog['order_net_amount']['date_basis']
    assert '退款' in catalog['net_cash_receipts']['definition']
    assert catalog['net_cash_receipts']['missing_policy'] == 'unknown'
    assert catalog['profit']['requires_policy_confirmation']
    assert catalog['average_order_amount']['aggregation'] == 'ratio_of_sums'
    assert catalog['repeat_customer_rate']['customer_key'] == 'customer_unique_id'
    assert catalog['late_rate']['date_comparison'] == 'calendar_date'


def test_catalog_snapshots_do_not_change_the_shared_definitions():
    first = metrics.reference_catalog()
    first[0]['definition'] = 'modified'
    assert metrics.reference_catalog()[0]['definition'] != 'modified'
    assert all(item['version'] == metrics.DEFINITION_VERSION for item in first)
