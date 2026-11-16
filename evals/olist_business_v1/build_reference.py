"""Recompute local full-Olist grading anchors; no model calls or source edits."""
import csv
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pandas as pd


HERE = Path(__file__).resolve().parent
RAW = HERE.parents[2] / "数据分析" / "data" / "raw"
FILES = {
    "orders": "olist_orders_dataset.csv", "items": "olist_order_items_dataset.csv",
    "payments": "olist_order_payments_dataset.csv", "reviews": "olist_order_reviews_dataset.csv",
    "customers": "olist_customers_dataset.csv", "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv", "translation": "product_category_name_translation.csv",
    "geolocation": "olist_geolocation_dataset.csv",
}


def cents(value):
    amount = Decimal(value) * 100
    assert amount == amount.to_integral_value(), value
    return int(amount)


def build():
    t = {name: pd.read_csv(RAW / file, dtype=str) for name, file in FILES.items()}
    o, i, p, r, c = (t[name].copy() for name in ("orders", "items", "payments", "reviews", "customers"))
    for field in ("price", "freight_value"):
        i[field + "_cents"] = i[field].map(cents)
    p["payment_cents"] = p.payment_value.map(cents)
    for field in o.columns[o.columns.str.contains("timestamp|_at$|_date$")]:
        o[field] = pd.to_datetime(o[field], errors="raise")
    for frame, keys in ((o, ["order_id"]), (c, ["customer_id"]),
                        (i, ["order_id", "order_item_id"]), (p, ["order_id", "payment_sequential"])):
        assert not frame.duplicated(keys).any()
    amounts = i.groupby("order_id").agg(item_cents=("price_cents", "sum"),
                freight_cents=("freight_value_cents", "sum"), item_lines=("order_item_id", "size"))
    paid = p.groupby("order_id").agg(payment_cents=("payment_cents", "sum"))
    latest = r.sort_values(["order_id", "review_answer_timestamp", "review_creation_date", "review_id"],
                           na_position="first").drop_duplicates("order_id", keep="last")
    assert not latest.order_id.duplicated().any()
    all_orders = o.merge(amounts, on="order_id", how="left", validate="one_to_one")
    all_orders = all_orders.merge(paid, on="order_id", how="left", validate="one_to_one")
    all_orders = all_orders.merge(c, on="customer_id", how="left", validate="one_to_one")
    all_orders = all_orders.merge(latest[["order_id", "review_score"]], on="order_id", how="left", validate="one_to_one")
    d = all_orders.loc[all_orders.order_status.eq("delivered")].copy()
    assert len(all_orders) == len(o)
    assert d[["item_cents", "freight_cents"]].notna().all().all()
    for field in ("item_cents", "freight_cents", "payment_cents", "item_lines"):
        d[field] = d[field].astype("Int64")
    d["purchase_month"] = d.order_purchase_timestamp.dt.strftime("%Y-%m")
    valid = d.order_delivered_customer_date.notna() & d.order_estimated_delivery_date.notna()
    timed = d.loc[valid].copy()
    timed["late"] = timed.order_delivered_customer_date.dt.normalize() > timed.order_estimated_delivery_date.dt.normalize()
    timed["delivery_days"] = (timed.order_delivered_customer_date - timed.order_purchase_timestamp).dt.total_seconds() / 86400
    reviewed = timed.loc[timed.review_score.notna()].copy()
    reviewed["low"] = reviewed.review_score.astype(int).le(2)
    summary = {
        "scope": "Full local 9 CSVs; delivered status for business metrics; BRL amounts stored as integer cents",
        "tables": {k: len(v) for k, v in t.items()},
        "purchase_min": str(o.order_purchase_timestamp.min()), "purchase_max": str(o.order_purchase_timestamp.max()),
        "status_counts": o.order_status.value_counts().to_dict(),
        "unique_customers_all": int(c.customer_unique_id.nunique()),
        "delivered_orders": len(d), "delivered_item_lines": int(d.item_lines.sum()),
        "delivered_item_cents": int(d.item_cents.sum()), "delivered_freight_cents": int(d.freight_cents.sum()),
        "delivered_payment_cents": int(d.payment_cents.sum()),
        "delivered_payment_known_orders": int(d.payment_cents.notna().sum()),
        "delivered_payment_unknown_orders": int(d.payment_cents.isna().sum()),
        "delivered_aov_brl": float(d.item_cents.sum() / len(d) / 100),
        "delivered_unique_customers": int(d.customer_unique_id.nunique()),
        "delivered_repeat_customers": int(d.groupby("customer_unique_id").size().ge(2).sum()),
        "multi_review_orders": int(r.groupby("order_id").size().gt(1).sum()),
        "delivered_valid_delivery_dates": len(timed), "delivered_missing_delivery_dates": int((~valid).sum()),
        "late_orders_calendar_date": int(timed.late.sum()),
        "late_rate_calendar_date": float(timed.late.mean()),
        "delivery_days_median": float(timed.delivery_days.median()), "delivery_days_p90": float(timed.delivery_days.quantile(.9)),
        "late_reviewed_orders": int(reviewed.late.sum()),
        "late_low_review_orders": int((reviewed.late & reviewed.low).sum()),
        "ontime_reviewed_orders": int((~reviewed.late).sum()),
        "ontime_low_review_orders": int((~reviewed.late & reviewed.low).sum()),
        "delivered_no_review": int(d.review_score.isna().sum()),
        "source_sha256": {file: hashlib.sha256((RAW / file).read_bytes()).hexdigest() for file in FILES.values()},
    }
    diff = d.payment_cents - d.item_cents - d.freight_cents
    summary["payment_reconciliation"] = {
        "difference_cents_known_orders": int(diff.sum()), "abs_difference_gt_one_cent_orders": int(diff.abs().gt(1).sum()),
        "unknown_orders": int(diff.isna().sum()),
        "zero_orders": int(diff.eq(0).sum()), "positive_orders": int(diff.gt(0).sum()), "negative_orders": int(diff.lt(0).sum()),
    }
    monthly = d.groupby("purchase_month").agg(orders=("order_id", "size"), item_cents=("item_cents", "sum"),
                freight_cents=("freight_cents", "sum"), payment_cents=("payment_cents", "sum"))
    monthly["aov_brl"] = monthly.item_cents / monthly.orders / 100
    june, july = monthly.loc["2018-06"], monthly.loc["2018-07"]
    n0, n1, a0, a1 = (int(june.orders), int(july.orders), int(june.item_cents), int(july.item_cents))
    count_effect = (n1 - n0) * (a0 / n0 + a1 / n1) / 2
    aov_effect = (a1 / n1 - a0 / n0) * (n0 + n1) / 2
    assert abs(count_effect + aov_effect - (a1 - a0)) < 1e-6
    summary["july_vs_june_2018"] = {"item_delta_cents": a1 - a0,
        "item_growth": a1 / a0 - 1, "order_growth": n1 / n0 - 1,
        "aov_growth": (a1 / n1) / (a0 / n0) - 1,
        "symmetric_count_effect_brl": count_effect / 100, "symmetric_aov_effect_brl": aov_effect / 100}
    # Observed-history segmentation uses purchase cutoff, not reconstructed as-of availability.
    cutoff = pd.Timestamp("2018-08-01")
    before = d.loc[d.order_purchase_timestamp.lt(cutoff)]
    people = before.groupby("customer_unique_id").agg(orders=("order_id", "size"),
                item_cents=("item_cents", "sum"), last_purchase=("order_purchase_timestamp", "max"))
    people["recency_days"] = (cutoff - people.last_purchase.dt.normalize()).dt.days
    people["segment"] = people.recency_days.le(90).map({True: "活跃", False: "沉默"}) + people.orders.ge(2).map({True: "复购", False: "一次"})
    segments = people.groupby("segment").agg(customers=("orders", "size"), orders=("orders", "sum"), item_cents=("item_cents", "sum"))
    lines = i.merge(d[["order_id", "customer_state"]], on="order_id", validate="many_to_one")
    lines = lines.merge(t["products"][["product_id", "product_category_name"]], on="product_id", how="left", validate="many_to_one")
    lines = lines.merge(t["translation"], on="product_category_name", how="left", validate="many_to_one")
    lines["category"] = lines.product_category_name_english.fillna(lines.product_category_name).fillna("Unknown_original_category")
    categories = lines.groupby("category").agg(item_cents=("price_cents", "sum"), orders=("order_id", "nunique"), item_lines=("order_item_id", "size")).sort_values("item_cents", ascending=False)
    sellers = lines.groupby("seller_id").agg(item_cents=("price_cents", "sum"), orders=("order_id", "nunique"), item_lines=("order_item_id", "size")).sort_values("item_cents", ascending=False)
    regions = d.groupby("customer_state").agg(orders=("order_id", "size"), item_cents=("item_cents", "sum"), freight_cents=("freight_cents", "sum")).sort_values("item_cents", ascending=False)
    paytypes = p.merge(d[["order_id"]], on="order_id", validate="many_to_one").groupby("payment_type").agg(payment_cents=("payment_cents", "sum"), orders=("order_id", "nunique"), records=("order_id", "size"))
    assert categories.item_cents.sum() == sellers.item_cents.sum() == regions.item_cents.sum() == d.item_cents.sum()
    assert paytypes.payment_cents.sum() == d.payment_cents.sum()
    # Independent stdlib Decimal reconciliation checks the consequential money totals.
    delivered_ids = set(d.order_id)
    for file, field, expected in ((FILES["items"], "price", d.item_cents.sum()),
                                  (FILES["items"], "freight_value", d.freight_cents.sum()),
                                  (FILES["payments"], "payment_value", d.payment_cents.sum())):
        with (RAW / file).open(encoding="utf-8", newline="") as source:
            total = sum((Decimal(row[field]) for row in csv.DictReader(source) if row["order_id"] in delivered_ids), Decimal(0))
        assert total * 100 == int(expected)
    manifest = json.loads((RAW.parent / "source_manifest.json").read_text(encoding="utf-8-sig"))
    for record in manifest["files"]:
        assert summary["source_sha256"][record["file"]] == record["sha256"].lower()
    for name, frame in (("monthly", monthly), ("categories", categories), ("sellers", sellers),
                        ("regions", regions), ("payment_types", paytypes), ("customer_segments", segments)):
        frame.to_csv(HERE / f"reference_{name}.csv", encoding="utf-8-sig")
    (HERE / "reference_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    build()
