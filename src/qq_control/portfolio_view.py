from __future__ import annotations

import json
from decimal import Decimal
from src.paths import (
    PROJECT_ROOT,
    PUBLIC_LEDGER_BENCHMARK_PATH,
    PUBLIC_LEDGER_STATE_PATH,
)


STATE_PATH = PUBLIC_LEDGER_STATE_PATH
BENCHMARK_PATH = PUBLIC_LEDGER_BENCHMARK_PATH
WATCHLIST_PATH = PROJECT_ROOT / "market_intelligence" / "watchlist.json"


def _decimal(value: object) -> Decimal:
    return Decimal(str(value or "0"))


def _live_navs() -> tuple[dict[str, tuple[str, Decimal]], str | None]:
    """Read refreshed official NAV observations without changing the ledger."""
    if not WATCHLIST_PATH.exists():
        return {}, None
    try:
        payload = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
        navs: dict[str, tuple[str, Decimal]] = {}
        for entry in payload.get("entries", []):
            latest = entry.get("fund_live", {}).get("latest") or {}
            code, date, nav = str(entry.get("fund_code", "")), latest.get("date"), latest.get("nav")
            if code and date and nav is not None:
                navs[code] = (str(date), _decimal(nav))
        as_of = max((date for date, _ in navs.values()), default=None)
        return navs, as_of
    except (OSError, ValueError, TypeError):
        return {}, None


def get_portfolio_dashboard() -> dict:
    if not STATE_PATH.exists():
        raise LookupError("模拟投资账本尚未初始化")

    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    valuations = state.get("valuations", [])
    latest = valuations[-1] if valuations else {
        "date": state["start_date"],
        "cash": state.get("cash_available", "0"),
        "market_value": "0",
        "total_assets": state.get("initial_cash", "0"),
        "return_percent": "0",
        "drawdown_percent": "0",
        "positions": {},
    }
    live_navs, live_as_of = _live_navs()
    initial = _decimal(state.get("initial_cash"))
    total = _decimal(latest.get("total_assets"))
    market_value = _decimal(latest.get("market_value"))
    cash = _decimal(latest.get("cash"))
    invested_capital = Decimal("0")

    positions = []
    for code, position in state.get("positions", {}).items():
        lots = position.get("lots", [])
        shares = sum((_decimal(lot.get("shares_remaining", lot.get("shares"))) for lot in lots), Decimal("0"))
        cost = sum((_decimal(lot.get("cost_remaining", lot.get("cost"))) for lot in lots), Decimal("0"))
        invested_capital += cost
        current = latest.get("positions", {}).get(code, {})
        if code in live_navs:
            nav_date, nav = live_navs[code]
            current = {"nav": str(nav), "market_value": str(shares * nav), "date": nav_date}
        value = _decimal(current.get("market_value"))
        pnl = value - cost
        positions.append({
            "code": code,
            "name": position.get("name", code),
            "shares": str(shares),
            "nav": str(current.get("nav", "0")),
            "cost": str(cost),
            "market_value": str(value),
            "pnl": str(pnl),
            "return_percent": str((pnl / cost * 100) if cost else Decimal("0")),
            "allocation_percent": str((value / total * 100) if total else Decimal("0")),
        })

    # The estimate reflects currently held shares, including frozen redemption
    # shares, plus available and frozen subscription cash.  It is intentionally
    # separate from the immutable end-of-day ledger valuation.
    if live_as_of and positions:
        market_value = sum((_decimal(item["market_value"]) for item in positions), Decimal("0"))
        cash = _decimal(state.get("cash_available")) + _decimal(state.get("cash_frozen"))
        total = cash + market_value
        live_return_percent = (total / initial - 1) * 100 if initial else Decimal("0")
    else:
        live_return_percent = _decimal(latest.get("return_percent"))

    benchmark_records = []
    if BENCHMARK_PATH.exists():
        benchmark_records = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8")).get("records", [])

    orders = sorted(state.get("orders", []), key=lambda item: item.get("decision_date", ""), reverse=True)
    effective_status: dict[str, str] = {}
    for annotation in state.get("decision_annotations", []):
        effective_status[annotation.get("decision_id")] = annotation.get("status", "ACTIVE")
    decisions = []
    for item in state.get("decisions", []):
        decision = dict(item)
        decision["status"] = effective_status.get(item.get("decision_id"), "ACTIVE")
        decisions.append(decision)
    known_ids = {item.get("decision_id") for item in decisions}
    for order in orders:
        decision_id = order.get("decision_id") or f"legacy-{order.get('order_id')}"
        if decision_id in known_ids:
            continue
        decisions.append({
            "decision_id": decision_id, "decision_date": order.get("decision_date"),
            "data_as_of": order.get("decision_date"), "action": order.get("side", "WATCH"),
            "market_observation": "早期账本未单独保存结构化市场观察，请查看关联证据。",
            "reason": order.get("thesis", "早期账本未记录决策理由"),
            "counter_evidence": "", "invalidation_conditions": "", "confidence": None,
            "evidence": order.get("evidence", []), "order_id": order.get("order_id"), "legacy": True,
        })
    orders_by_decision: dict[str, list[dict]] = {}
    for order in orders:
        decision_id = order.get("decision_id") or f"legacy-{order.get('order_id')}"
        orders_by_decision.setdefault(decision_id, []).append(order)
    for decision in decisions:
        linked_orders = orders_by_decision.get(decision.get("decision_id"), [])
        if linked_orders:
            decision["operations"] = [{
                "side": order.get("side"), "status": order.get("status"),
                "fund_code": order.get("fund_code"), "fund_name": order.get("fund_name"),
                "gross_amount": order.get("gross_amount"), "shares": order.get("shares"),
                "nav": order.get("nav"), "nav_date": order.get("nav_date"),
            } for order in linked_orders]
        else:
            decision["operations"] = [{
                "side": "WATCH", "status": "NO_ORDER",
                "description": "继续持有，本次未产生买卖订单",
            }]
    decisions.sort(key=lambda item: (item.get("decision_date", ""), item.get("recorded_at", "")), reverse=True)
    return {
        "status": state.get("status", "UNKNOWN"),
        "period": {"start": state.get("start_date"), "end": state.get("end_date"), "as_of": latest.get("date")},
        "display_valuation": {
            "source": "live_estimate" if live_as_of else "official_ledger",
            "as_of": live_as_of or latest.get("date"),
            "official_ledger_as_of": latest.get("date"),
            "note": "基于账本份额和最新官方基金净值的展示估算；不结算订单、不写入账本。" if live_as_of else "账本正式日终估值。",
        },
        "summary": {
            "initial_cash": str(initial),
            "total_assets": str(total),
            "cash": str(cash),
            "cash_frozen": state.get("cash_frozen", "0"),
            "market_value": str(market_value),
            "invested_capital": str(invested_capital),
            "total_pnl": str(total - initial),
            "return_percent": str(live_return_percent),
            "drawdown_percent": latest.get("drawdown_percent", "0"),
            "invested_percent": str((invested_capital / initial * 100) if initial else Decimal("0")),
            "market_weight_percent": str((market_value / total * 100) if total else Decimal("0")),
        },
        "positions": positions,
        "valuations": valuations,
        "orders": orders,
        "decisions": decisions,
        "benchmarks": benchmark_records,
    }
