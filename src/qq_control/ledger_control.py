"""Local-only public-AI control surface.

The QQ/Codex bridge talks to this module through HTTP.  It never needs direct
filesystem or CLI access to the public portfolio ledger.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from src.qq_control.paper_ledger import PaperLedger, fetch_trading_dates, order_schedule_after_cutoff
from src.qq_control.portfolio_view import get_portfolio_dashboard
from src.paths import PUBLIC_LEDGER_FEE_ROOT, PUBLIC_LEDGER_STATE_PATH


STATE_PATH = PUBLIC_LEDGER_STATE_PATH
FEE_ROOT = PUBLIC_LEDGER_FEE_ROOT


def _ledger() -> PaperLedger:
    if not STATE_PATH.exists():
        raise LookupError("public portfolio ledger has not been initialized")
    return PaperLedger(STATE_PATH)


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _schedule(decision_date: str) -> tuple[str, str]:
    dates = fetch_trading_dates(decision_date, "2027-12-31")
    return order_schedule_after_cutoff(decision_date, dates)


def portfolio() -> dict:
    return get_portfolio_dashboard()


def decisions() -> list[dict]:
    state = _ledger().state
    statuses: dict[str, str] = {}
    for annotation in state.get("decision_annotations", []):
        statuses[annotation.get("decision_id", "")] = annotation.get("status", "ACTIVE")
    return [{**item, "status": statuses.get(item.get("decision_id", ""), "ACTIVE")}
            for item in state.get("decisions", [])]


def orders() -> list[dict]:
    return _ledger().state.get("orders", [])


def record_decision(payload: dict) -> dict:
    required = ("action", "market_observation", "reason", "confidence", "user_confirmation")
    missing = [key for key in required if payload.get(key) in (None, "")]
    if missing:
        raise ValueError(f"missing decision fields: {', '.join(missing)}")
    today = date.today().isoformat()
    ledger = _ledger()
    return ledger.record_decision(
        payload.get("decision_id") or _id("D"), payload.get("decision_date") or today,
        str(payload["action"]), str(payload["market_observation"]), str(payload["reason"]),
        int(payload["confidence"]), list(payload.get("evidence") or []),
        str(payload.get("counter_evidence") or ""), str(payload.get("invalidation_conditions") or ""),
        str(payload.get("data_as_of") or payload.get("decision_date") or today),
        str(payload["user_confirmation"]),
    )


def void_decision(decision_id: str, payload: dict) -> dict:
    confirmation = str(payload.get("user_confirmation") or "")
    reason = str(payload.get("reason") or "")
    if not confirmation or not reason:
        raise ValueError("reason and user_confirmation are required")
    return _ledger().annotate_decision(payload.get("annotation_id") or _id("A"), decision_id,
                                       str(payload.get("status") or "VOIDED"), reason, confirmation)


def _order_payload(payload: dict, side: str) -> dict:
    required = ("decision_id", "fund_code", "thesis")
    missing = [key for key in required if not payload.get(key)]
    if missing:
        raise ValueError(f"missing order fields: {', '.join(missing)}")
    decision_date = str(payload.get("decision_date") or date.today().isoformat())
    nav_date, confirmation_date = _schedule(decision_date)
    ledger = _ledger()
    order_id = str(payload.get("order_id") or _id("B" if side == "BUY" else "S"))
    if side == "BUY":
        if payload.get("amount") in (None, "") or not payload.get("fund_name"):
            raise ValueError("amount and fund_name are required for buy/add")
        ledger.register_buy(order_id, decision_date, nav_date, confirmation_date,
                            str(payload["fund_code"]), str(payload["fund_name"]), Decimal(str(payload["amount"])),
                            Decimal(str(payload.get("subscription_fee_rate", "0"))), list(payload.get("evidence") or []),
                            str(payload["thesis"]), str(payload["decision_id"]))
    else:
        shares = payload.get("shares")
        if shares in (None, ""):
            raise ValueError("shares are required for reduce/sell")
        fee_path = FEE_ROOT / f"{payload['fund_code']}.json"
        if not fee_path.exists():
            raise ValueError("no audited redemption fee schedule for fund")
        import json
        schedule = json.loads(fee_path.read_text(encoding="utf-8"))["redemption"]
        ledger.register_sell(order_id, decision_date, nav_date, confirmation_date, str(payload["fund_code"]),
                             Decimal(str(shares)), schedule, list(payload.get("evidence") or []),
                             str(payload["thesis"]), str(payload["decision_id"]))
    return next(item for item in ledger.state["orders"] if item["order_id"] == order_id)


def buy(payload: dict) -> dict:
    return _order_payload(payload, "BUY")


def sell(payload: dict) -> dict:
    return _order_payload(payload, "SELL")


def liquidate(fund_code: str, payload: dict) -> dict:
    ledger = _ledger()
    position = ledger.state.get("positions", {}).get(fund_code)
    if position is None:
        raise ValueError("no position to liquidate")
    total = sum((Decimal(str(lot.get("shares_remaining", lot["shares"]))) for lot in position.get("lots", [])), Decimal("0"))
    frozen = Decimal(str(position.get("shares_frozen", "0")))
    available = total - frozen
    if available <= 0:
        raise ValueError("no available shares to liquidate")
    return sell({**payload, "fund_code": fund_code, "shares": str(available)})


def cancel_order(order_id: str, payload: dict) -> dict:
    reason = str(payload.get("reason") or "")
    confirmation = str(payload.get("user_confirmation") or "")
    return _ledger().cancel_order(order_id, reason, confirmation)


def settle(as_of: str) -> dict:
    ledger = _ledger()
    settled = ledger.settle_due_buys(as_of) + ledger.settle_due_sells(as_of)
    return {"as_of": as_of, "settled": settled, "summary": ledger.summary()}
