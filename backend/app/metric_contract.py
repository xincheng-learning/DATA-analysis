"""Bind supported declared calculations to confirmed catalog definitions.

This checks formulas within the declared order scope, not arbitrary user intent.
"""

from app.calculation_contract import CalculationMismatch, CalculationPlan, compile_plan
from app.metrics import DEFINITION_VERSION, METRICS
from app.tools.join_validation import _quote


class MetricDefinitionMismatch(CalculationMismatch):
    check_id = "metric_definition"
    retryable = False


def verify_metric_definition(dataset, brief, plan, reference_checks):
    from app.answer_acceptance import metric_id
    from app.commerce import detect_olist
    from app.tools.relationships import commerce_confirmed

    specs = brief.get("acceptance", [])
    ids = list(dict.fromkeys(m for spec in specs for m in spec.get("metric_ids", [])))
    if not ids:
        return []
    refs = [spec["id"] for spec in specs if set(spec.get("metric_ids", [])) & set(ids)]
    scope = "核对当前数据下的结果与字典约束声明计算的一致性；不证明候选SQL/Python在其他数据上等价，用户意图、期间选择及结论解释未独立认证。"

    def receipt(status, detail):
        return [
            {
                "id": "metric_definition",
                "status": status,
                "metric_ids": ids,
                "requirement_ids": refs,
                "definition_version": DEFINITION_VERSION,
                "scope": scope,
                "detail": detail,
            }
        ]

    if not commerce_confirmed(dataset):
        return receipt(
            "not_checked", "指标口径未核验：当前数据版本尚未确认 Olist/BRL 模板；已有结果仅作探索。"
        )
    # shortcut: single-stage scalar catalog metrics only; add grouped/multi-stage contracts when needed.
    if (
        len(plan.stages) != 1
        or plan.stages[0].group_by
        or any(not METRICS.get(m, {}).get("calculation") for m in ids)
    ):
        return receipt(
            "not_checked", "指标口径未核验：此公式、分组或多阶段计划尚未接入确定性的业务定义核对。"
        )
    stage = plan.stages[0]
    roles = detect_olist(dataset.files)
    required = {"orders", *(METRICS[m]["calculation"]["role"] for m in ids)}
    if not required <= roles.keys():
        return receipt("not_checked", "指标口径未核验：本轮所需的真实表角色尚未完整绑定。")
    if len(required - {"orders"}) > 1:
        return receipt(
            "not_checked", "指标口径未核验：多个事实来源的组合尚未接入本轮简单指标核对。"
        )
    tables = {stage.source, *(join.table for join in stage.joins)}
    allowed = {roles[role] for role in required}
    if tables != allowed or len(stage.joins) != len(allowed) - 1:
        return receipt(
            "not_checked", "指标口径未核验：此来源或附加关联形态尚未接入已确认的简单指标核对。"
        )
    available = {
        file.table_name: set(file.columns) for file in dataset.files if file.table_name in tables
    }

    def qualified(field):
        if "." in field:
            return field
        owners = [table for table, columns in available.items() if field in columns]
        return owners[0] + "." + field if len(owners) == 1 else field

    orders = roles["orders"]
    status = orders + ".order_status"
    statuses = [predicate for predicate in stage.filters if qualified(predicate.field) == status]
    if not statuses or any(p.op != "eq" or p.value != "delivered" or p.cast for p in statuses):
        raise MetricDefinitionMismatch(
            "指标口径不符：已确认指标只统计已送达订单，计划缺少或改变了该状态筛选。"
        )
    supported_scope = {
        status,
        orders + ".order_purchase_timestamp",
        orders + ".order_id",
        orders + ".customer_id",
    }
    if any(qualified(predicate.field) not in supported_scope for predicate in stage.filters):
        return receipt(
            "not_checked",
            "指标口径未核验：附加筛选或日期归属超出当前已支持的下单时间/订单标识范围。",
        )
    for join in stage.joins:
        left = [key if "." in key else stage.source + "." + key for key in join.left_keys]
        right = [key if "." in key else join.table + "." + key for key in join.right_keys]
        if (
            join.kind not in {"inner", "left"}
            or left != [stage.source + ".order_id"]
            or right != [join.table + ".order_id"]
        ):
            raise MetricDefinitionMismatch("指标口径不符：订单与明细必须按真实 order_id 关联。")

    targets = {}
    for item in stage.select:
        inferred = metric_id(item.name)
        matches = {
            m
            for spec in specs
            if len(spec.get("metric_ids", [])) == 1 and spec.get("result_columns") == [item.name]
            for m in spec["metric_ids"]
        }
        if inferred in ids:
            matches.add(inferred)
        if len(matches) != 1:
            return receipt("not_checked", "指标口径未核验：部分输出列无法唯一对应本轮指标定义。")
        identifier = matches.pop()
        definition = METRICS[identifier]["calculation"]
        if (
            item.op != definition["op"]
            or qualified(item.field) != roles[definition["role"]] + "." + definition["field"]
        ):
            raise MetricDefinitionMismatch(
                "指标口径不符："
                + METRICS[identifier]["label"]
                + "的聚合方式或实际金额/主键字段与字典不一致。"
            )
        targets[identifier] = item
    if set(targets) != set(ids):
        return receipt("not_checked", "指标口径未核验：实际输出尚未覆盖本轮请求的全部指标。")
    if any(
        check.get("id") == "aggregate_coverage" and check.get("status") != "passed"
        for check in reference_checks
    ):
        return receipt(
            "not_checked", "指标口径未核验：聚合输入存在缺失或覆盖缺口；当前数值不能当完整总额。"
        )

    fact_role = next(iter(required - {"orders"}), None)
    probe = stage.model_copy(deep=True)
    probe.source = orders
    if fact_role:
        from app.calculation_contract import Expression, Join

        fact = roles[fact_role]
        probe.joins = [
            Join(table=fact, kind="left", left_keys=["order_id"], right_keys=["order_id"])
        ]
        field = next(
            item.field
            for identifier, item in targets.items()
            if METRICS[identifier]["calculation"]["role"] == fact_role
        )
        probe.select = [
            Expression(name="total", op="count_rows"),
            Expression(name="known", op="count", field=qualified(field)),
        ]
    else:
        from app.calculation_contract import Expression

        probe.select = [
            Expression(name="total", op="count_rows"),
            Expression(name="known", op="count", field=orders + ".order_id"),
        ]
    probe.order_by = []
    query, _ = compile_plan(
        dataset,
        CalculationPlan(source_quotes=plan.source_quotes, stages=[probe]),
        "\n".join(plan.source_quotes),
    )
    key_roles = {"orders": ["order_id"]}
    if fact_role == "order_items":
        key_roles["order_items"] = ["order_id", "order_item_id"]
    invalid = []
    for role, keys in key_roles.items():
        names = ",".join(_quote(key) for key in keys)
        invalid.append(
            "EXISTS(SELECT 1 FROM "
            + _quote(roles[role])
            + " GROUP BY "
            + names
            + " HAVING COUNT(*)>1 OR "
            + " OR ".join(_quote(key) + " IS NULL" for key in keys)
            + ")"
        )
    row = dataset.execute(
        "SELECT p.*,(" + " OR ".join(invalid) + ") AS invalid_keys FROM (" + query + ") p",
        as_frame=True,
    ).iloc[0]
    if row["invalid_keys"] or row["total"] != row["known"] or fact_role and not row["total"]:
        return receipt(
            "not_checked", "指标口径未核验：业务键缺失/重复、关联未覆盖或范围为空；未认证完整指标。"
        )
    return receipt(
        "passed",
        "本轮结果已与受指标字典约束的声明计算对照；已核对声明中的聚合、字段、已送达状态、关联键及实际输入覆盖和业务键。",
    )
