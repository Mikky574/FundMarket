from decimal import Decimal

import pytest

import pandas as pd

from app.paper_engine import PaperLedger, order_schedule_after_cutoff


def test_order_schedule_after_cutoff():
    assert order_schedule_after_cutoff(
        "2026-07-22", ["2026-07-22", "2026-07-23", "2026-07-24"]
    ) == ("2026-07-23", "2026-07-24")


def test_ledger_freezes_cash(tmp_path):
    ledger = PaperLedger(tmp_path / "state.json")
    ledger.initialize("2026-07-22", "2026-08-21", Decimal("100000"))
    ledger.register_buy("O1", "2026-07-22", "2026-07-23", "2026-07-24",
                        "018099", "测试基金", Decimal("20000"), Decimal("0"), [], "test")
    order = ledger.state["orders"][0]
    assert order["nav_date"] == "2026-07-23"
    assert order["confirmation_date"] == "2026-07-24"
    assert "execution_date" not in order
    assert ledger.summary()["cash_available"] == "80000.00"
    assert ledger.summary()["cash_frozen"] == "20000.00"
    with pytest.raises(ValueError):
        ledger.register_buy("O1", "2026-07-22", "2026-07-23", "2026-07-24",
                            "018099", "测试基金", Decimal("1"), Decimal("0"), [], "test")


def test_decision_is_immutable_and_order_action_must_match(tmp_path):
    ledger = PaperLedger(tmp_path / "state.json")
    ledger.initialize("2026-07-22", "2026-08-21", Decimal("100000"))
    decision = ledger.record_decision(
        "D-20260722-001", "2026-07-22", "BUY", "保险板块相对强势", "满足建仓规则", 68,
        ["paper/data/market.json"], "短期涨幅偏高", "行业趋势转弱", "2026-07-22", "用户确认写入",
    )
    assert decision["market_observation"] == "保险板块相对强势"
    with pytest.raises(ValueError, match="已经存在"):
        ledger.record_decision("D-20260722-001", "2026-07-22", "BUY", "重复", "重复", 50,
                               user_confirmation="用户确认写入")
    with pytest.raises(ValueError, match="动作"):
        ledger.register_sell("S1", "2026-07-22", "2026-07-23", "2026-07-24", "018099",
                             Decimal("1"), [], [], "不应允许", "D-20260722-001")
    ledger.register_buy("O1", "2026-07-22", "2026-07-23", "2026-07-24", "018099", "测试基金",
                        Decimal("20000"), Decimal("0"), [], "满足建仓规则", "D-20260722-001")
    assert ledger.state["orders"][0]["decision_id"] == "D-20260722-001"


def test_one_rebalance_decision_can_link_multiple_buy_and_sell_orders(tmp_path):
    ledger = PaperLedger(tmp_path / "state.json")
    ledger.initialize("2026-07-22", "2026-08-21", Decimal("100000"))
    ledger.state["positions"] = {"018099": {"name": "测试基金", "shares_frozen": "0.0000", "lots": [
        {"order_id": "B0", "confirmation_date": "2026-07-21", "nav": "1", "shares": "1000.0000",
         "shares_remaining": "1000.0000", "cost": "1000.00", "cost_remaining": "1000.00", "fee": "0"}
    ]}}
    ledger.record_decision("D-REB-1", "2026-07-22", "REBALANCE", "风险预算调整", "降低集中度", 70,
                           user_confirmation="用户确认当日再平衡订单")
    ledger.register_sell("S-REB-1", "2026-07-22", "2026-07-23", "2026-07-24", "018099", Decimal("300"),
                         [], [], "降低原持仓", "D-REB-1")
    ledger.register_buy("B-REB-1", "2026-07-22", "2026-07-23", "2026-07-24", "007467", "测试基金二",
                        Decimal("5000"), Decimal("0"), [], "建立分散仓位", "D-REB-1")
    assert {order["side"] for order in ledger.state["orders"]} == {"BUY", "SELL"}
    assert {order["decision_id"] for order in ledger.state["orders"]} == {"D-REB-1"}


def test_decision_requires_confirmation_and_duplicate_can_be_voided(tmp_path):
    ledger = PaperLedger(tmp_path / "state.json")
    ledger.initialize("2026-07-22", "2026-08-21", Decimal("100000"))
    with pytest.raises(ValueError, match="尚未明确确认"):
        ledger.record_decision("D1", "2026-07-22", "WATCH", "观察", "等待", 70)
    ledger.record_decision("D1", "2026-07-22", "WATCH", "观察", "等待", 70,
                           user_confirmation="用户确认写入")
    annotation = ledger.annotate_decision(
        "A1", "D1", "VOIDED_DUPLICATE", "误将讨论记录为正式决策", "用户确认本次更正"
    )
    assert annotation["status"] == "VOIDED_DUPLICATE"
    assert ledger.decision_status("D1") == "VOIDED_DUPLICATE"
    assert ledger.verify_audit()["valid"] is True


def test_same_day_decision_can_be_superseded_without_an_order(tmp_path):
    ledger = PaperLedger(tmp_path / "state.json")
    ledger.initialize("2026-07-22", "2026-08-21", Decimal("100000"))
    ledger.record_decision("D1", "2026-07-22", "WATCH", "初始观察", "等待确认", 60,
                           invalidation_conditions="趋势转弱", user_confirmation="用户确认初始观察")
    ledger.annotate_decision("A1", "D1", "VOIDED_SUPERSEDED", "趋势转弱，用户要求重新决策", "用户确认替代初始决策")
    replacement = ledger.record_decision("D2", "2026-07-22", "REDUCE", "复核后趋势转弱", "满足减仓条件", 72,
                                         user_confirmation="用户确认新决策")
    assert replacement["decision_id"] == "D2"
    assert ledger.decision_status("D1") == "VOIDED_SUPERSEDED"
    assert ledger.decision_status("D2") == "ACTIVE"


def test_buy_uses_nav_date_before_confirmation_date(tmp_path, monkeypatch):
    ledger = PaperLedger(tmp_path / "state.json")
    ledger.initialize("2026-07-22", "2026-08-21", Decimal("100000"))
    ledger.register_buy("O1", "2026-07-22", "2026-07-23", "2026-07-24",
                        "018099", "测试基金", Decimal("20000"), Decimal("0"), [], "test")
    frame = pd.DataFrame([{"净值日期": "2026-07-23", "单位净值": 1.25}])
    monkeypatch.setattr("app.paper_engine.ak.fund_open_fund_info_em", lambda *_: frame)

    transactions = ledger.settle_due_buys("2026-07-23")

    assert transactions[0]["nav_date"] == "2026-07-23"
    assert transactions[0]["confirmation_date"] == "2026-07-24"
    lot = ledger.state["positions"]["018099"]["lots"][0]
    assert lot["confirmation_date"] == "2026-07-24"
    assert lot["shares"] == "16000.0000"


def test_fifo_sell_fee_and_valuation(tmp_path):
    ledger = PaperLedger(tmp_path / "state.json")
    ledger.initialize("2026-07-01", "2026-08-01", Decimal("100000"))
    ledger.state["cash_available"] = "80000.00"
    ledger.state["positions"] = {"018099": {"name": "测试基金", "shares_frozen": "0.0000", "lots": [
        {"order_id": "B1", "confirmation_date": "2026-07-01", "nav": "1.0", "shares": "10000.0000",
         "shares_remaining": "10000.0000", "cost": "10000.00", "cost_remaining": "10000.00", "fee": "0.00"},
        {"order_id": "B2", "confirmation_date": "2026-07-20", "nav": "1.0", "shares": "10000.0000",
         "shares_remaining": "10000.0000", "cost": "10000.00", "cost_remaining": "10000.00", "fee": "0.00"},
    ]}}
    schedule = [{"min_days": 0, "max_days_exclusive": 7, "rate": 0.015},
                {"min_days": 7, "max_days_exclusive": 30, "rate": 0.005},
                {"min_days": 30, "max_days_exclusive": None, "rate": 0}]
    ledger.register_sell("S1", "2026-07-20", "2026-07-21", "2026-07-22",
                         "018099", Decimal("15000"), schedule, [], "test")
    tx = ledger.settle_sell(ledger.state["orders"][-1], Decimal("1.10"))
    assert tx["fee"] == "137.50"  # 55元（21天）+ 82.50元（2天）
    assert tx["lots"][0]["shares"] == "10000.0000"
    assert tx["lots"][1]["shares"] == "5000.0000"


def test_daily_valuation_cannot_be_overwritten(tmp_path):
    ledger = PaperLedger(tmp_path / "state.json")
    ledger.initialize("2026-07-01", "2026-08-01", Decimal("100000"))
    first = ledger.record_valuation("2026-07-01", {})
    assert first["total_assets"] == "100000.00"
    with pytest.raises(ValueError):
        ledger.record_valuation("2026-07-01", {})
    audit = (tmp_path / "state.audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(audit) == 2
    assert ledger.verify_audit()["valid"] is True


def test_audit_detects_manual_state_edit(tmp_path):
    path = tmp_path / "state.json"
    ledger = PaperLedger(path)
    ledger.initialize("2026-07-01", "2026-08-01", Decimal("100000"))
    state = path.read_text(encoding="utf-8").replace("100000.00", "999999.00", 1)
    path.write_text(state, encoding="utf-8")
    altered = PaperLedger(path)
    assert altered.verify_audit()["valid"] is False


def test_daily_close_rejects_future_date(tmp_path):
    ledger = PaperLedger(tmp_path / "state.json")
    ledger.initialize("2026-07-01", "2999-08-01", Decimal("100000"))
    with pytest.raises(ValueError, match="未来"):
        ledger.daily_close("2999-07-01")
