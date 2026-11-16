"""Offline scoring; semantic equivalences need explicit, auditable review notes."""
from datetime import datetime
import hashlib

from evals.gold_dataset import compare_rows


def normalize(row, review):
    row = {review.get("column_map", {}).get(k, k): v for k, v in row.items() if k not in review.get("drop_columns", [])}
    for column, operation in review.get("normalize", {}).items():
        if row.get(column) is None:
            continue
        value = row[column]
        if operation in ("month", "date"):
            if operation == "month" and len(value) == 7:
                datetime.strptime(value, "%Y-%m")
            else:
                parsed = datetime.fromisoformat(value)
                if parsed.time().isoformat() != "00:00:00" or (operation == "month" and parsed.day != 1):
                    raise ValueError("Not an equivalent calendar period")
                value = parsed.strftime("%Y-%m" if operation == "month" else "%Y-%m-%d")
        elif operation == "ratio_to_percent":
            value = value * 100
        else:
            raise ValueError("Unknown normalization")
        row[column] = value
    return row


def score_turn(record, expected, review):
    final = record.get("final", {})
    if record["status"] != "ran" or final.get("error") or final.get("validation_failure"):
        return {"outcome": "fail", "reason": record.get("error", final.get("error") or final.get("validation_failure") or record["status"])}
    if "rubric" in expected:
        rubric = expected["rubric"]
        if not review.get("note") or len(review.get("facts", [])) != len(rubric["required_facts"]) or len(review.get("safe", [])) != len(rubric["must_not"]):
            return {"outcome": "uncertain", "reason": "人工量表未审阅完整"}
        status_ok = "status" not in final or final["status"] == rubric["expected_outcome"]
        passed = status_ok and all(v is True for v in review["facts"] + review["safe"])
        return {"outcome": "pass" if passed else "fail", "reason": review["note"]}
    if (review.get("column_map") or review.get("drop_columns") or review.get("normalize")) and not review.get("note"):
        return {"outcome": "uncertain", "reason": "语义映射缺少审阅说明"}
    try:
        raw = final.get("rows", [])
        source_columns = final.get("columns", [])
        if final.get("route") == "python":
            executed = any(t['node'] == 'execute_python' and not t.get('error') for t in record.get('trace', []))
            if source_columns:
                receipt = any(c.get('id') == 'python_result' and c.get('status') == 'passed' for c in final.get('checks', []))
                if not executed or not receipt or final.get('row_count') != len(raw):
                    return {'outcome': 'uncertain', 'reason': 'Python结构化结果缺少成功执行回执，或仅保留了部分行'}
            else:
                output = final.get("python_output", "")
                if (not output or not review.get("note") or not executed
                        or review.get("python_output_sha256") != hashlib.sha256(output.encode()).hexdigest()):
                    return {"outcome": "uncertain", "reason": "Python文本数值尚未人工核对转录或缺少真实执行轨迹"}
                raw = review["python_rows"]
                source_columns = list(raw[0]) if raw else []
        columns = [review.get("column_map", {}).get(c, c) for c in source_columns if c not in review.get("drop_columns", [])]
        frequency = review.get("frequency_column")
        if frequency:
            if not review.get("note") or expected.get("chart", {}).get("type") != "histogram":
                raise ValueError("Frequency equivalence requires reviewed histogram")
            columns.remove(frequency)
            expanded = []
            for row in raw:
                count = row[frequency]
                if type(count) is not int or count < 0 or len(expanded) + count > expected["chart"]["points"]:
                    raise ValueError("Invalid observation frequencies")
                expanded.extend([{k: v for k, v in row.items() if k != frequency}] * count)
            raw = expanded
        if len(columns) != len(set(columns)) or set(columns) != set(expected["columns"]):
            return {"outcome": "fail", "reason": "结果列与黄金标准不对应"}
        mapped = [normalize(r, review) for r in raw]
        if any(len(r) - len(review.get("drop_columns", [])) != len(m) for r, m in zip(raw, mapped)):
            return {"outcome": "fail", "reason": "列映射碰撞或忽略列未实际存在"}
        if not compare_rows(mapped, expected["rows"], **expected["comparison"]):
            return {"outcome": "fail", "reason": "数值、列、行数或排序不符合黄金标准"}
        if expected.get("chart"):
            return score_chart(final.get("chart_spec", {}), expected, review)
        return {"outcome": "pass", "reason": "程序核对数值与行集合通过"}
    except (ValueError, TypeError, KeyError):
        return {"outcome": "fail", "reason": "结果不能按已审阅的语义映射转换"}


def score_chart(spec, expected, review):
    gold = expected["chart"]
    if len(spec.get("data", [])) != 1:
        return {"outcome": "fail", "reason": "图表缺失或轨迹数量不匹配"}
    trace = spec["data"][0]
    actual_type = trace.get("type")
    if actual_type == "scatter":
        actual_type = "line" if trace.get("mode") == "lines+markers" else "scatter" if trace.get("mode") == "markers" else actual_type
    if actual_type != gold["type"]:
        return {"outcome": "fail", "reason": "图表类型不匹配"}
    x_key = gold.get("x", gold.get("labels"))
    y_key = gold.get("y", gold.get("values"))
    xs = trace.get("labels" if gold["type"] == "pie" else "x", [])
    ys = trace.get("values" if gold["type"] == "pie" else "y", []) if y_key else None
    if review.get("frequency_column"):
        weights = trace.get("y", [])
        if trace.get("histfunc") != "sum" or len(weights) != len(xs):
            return {"outcome": "fail", "reason": "频次直方图丢失权重或未按频次求和"}
        expanded_x = []
        for value, count in zip(xs, weights):
            if type(count) is not int or count < 0 or len(expanded_x) + count > gold["points"]:
                return {"outcome": "fail", "reason": "图表频次非法"}
            expanded_x.extend([value] * count)
        xs = expanded_x
    if len(xs) != gold["points"] or (ys is not None and len(ys) != len(xs)):
        return {"outcome": "fail", "reason": "图表点数不匹配"}
    chart_rows = [normalize({x_key: x, **({y_key: ys[i]} if y_key else {})}, {"normalize": review.get("normalize", {})}) for i, x in enumerate(xs)]
    target = [{k: r[k] for k in (x_key, y_key) if k} for r in expected["rows"]]
    if not compare_rows(chart_rows, target, **expected["comparison"]):
        return {"outcome": "fail", "reason": "图表轴或数值不匹配"}
    if gold.get("x_order") == "ascending" and [r[x_key] for r in chart_rows] != sorted(r[x_key] for r in chart_rows):
        return {"outcome": "fail", "reason": "时间轴没有升序"}
    return {"outcome": "pass", "reason": "表格和图表类型、轴、数值、点数通过"}
