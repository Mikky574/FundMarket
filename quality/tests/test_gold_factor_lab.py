from datetime import date, datetime, timedelta, timezone

from demos.gold_factor_lab.analysis import describe
from demos.gold_factor_lab import collector
from demos.gold_factor_lab.history_chart import build_html
from demos.gold_factor_lab.blind_replay import Decision, _daily_rows, _macro_context, anonymised_prompt, local_tool_decision, replay, third_prior_month
from tools.deepseek_blind_gold_tool import invoke
from demos.gold_factor_lab.factor_calibration import calibrate
from demos.gold_factor_lab.evaluation_report import build_html as build_evaluation_html
from demos.gold_factor_lab.swing_replay import replay as swing_replay
from demos.gold_factor_lab.swing_v3 import PositionState, _promote_value_to_core, _standardised_observations, _trim_core, features as swing_v3_features, replay as swing_v3_replay
from src.quant_research.contracts import BlindGoldAnalysisContext
from src.quant_research.intelligence import analyse_blind_gold


def test_jd_collector_marks_history_as_known_only_at_retrieval(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"success": True, "resultData": {"data": {"line": [
                {"date": "20260803", "price": "879.46"},
            ]}}}

    monkeypatch.setattr(collector.httpx, "post", lambda *_args, **_kwargs: Response())
    received = datetime(2026, 9, 3, 10, tzinfo=timezone.utc)
    row = collector.collect_jd_month(retrieved_at=received)[0]
    assert row["observed_on"] == "2026-08-03"
    assert row["available_at"] == received.isoformat()


def test_jd_history_accepts_six_month_period(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"success": True, "resultData": {"data": {"line": [
                {"date": "20260603", "price": "800.00"},
            ]}}}

    observed = {}
    def post(_url, **kwargs):
        observed.update(kwargs["json"])
        return Response()
    monkeypatch.setattr(collector.httpx, "post", post)
    rows = collector.collect_jd_history(period_type="m6")
    assert observed["periodType"] == "m6"
    assert rows[0]["observed_on"] == "2026-06-03"


def test_factor_panel_uses_one_year_source_for_an_old_short_window(monkeypatch):
    observed = {}
    def history(*, period_type, **_kwargs):
        observed["period"] = period_type
        return []
    monkeypatch.setattr(collector, "collect_jd_history", history)
    monkeypatch.setattr(collector, "collect_fred_factor", lambda *_args, **_kwargs: [{"observed_on": "2025-10-01", "value": 1}])
    try:
        collector.collect_factor_panel(start=date(2025, 10, 1), end=date(2025, 12, 31))
    except collector.CollectionError:
        pass
    assert observed["period"] == "y1"


def test_jd_intraday_collector_preserves_source_timestamp(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"success": True, "resultData": {"data": {"dataList": [
                {"goldPriceTime": "2026-09-03 10:01:00", "goldPrice": "945.43", "highKey": True},
            ]}}}

    monkeypatch.setattr(collector.httpx, "post", lambda *_args, **_kwargs: Response())
    row = collector.collect_jd_intraday(retrieved_at=datetime(2026, 9, 3, 2, 2, tzinfo=timezone.utc))[0]
    assert row["source_at"] == "2026-09-03T10:01:00+08:00"
    assert row["is_high"] is True


def test_describe_reports_gold_return_and_does_not_make_a_signal():
    panel = {
        "jd_zheshang_gold": [
            {"observed_on": f"2026-08-{day:02d}", "value": 100 + day}
            for day in range(1, 12)
        ],
        "us_10y_nominal_yield": [
            {"observed_on": f"2026-08-{day:02d}", "value": 4 + day / 100}
            for day in range(1, 12)
        ],
    }
    result = describe(panel)
    assert result["scope"].startswith("descriptive_only")
    assert result["gold"]["return_percent"] == 9.901
    assert "signal" not in result


def test_history_chart_separates_units_and_states_attribution_limit():
    panel = {
        "jd_zheshang_gold": [
            {"observed_on": "2026-08-01", "value": 800, "source": "jd"},
            {"observed_on": "2026-08-02", "value": 808, "source": "jd"},
        ],
        "usd_cny": [
            {"observed_on": "2026-08-01", "value": 7.1, "source": "fred"},
            {"observed_on": "2026-08-02", "value": 7.2, "source": "fred"},
        ],
        "us_10y_nominal_yield": [
            {"observed_on": "2026-08-01", "value": 4.2, "source": "fred"},
            {"observed_on": "2026-08-02", "value": 4.1, "source": "fred"},
        ],
    }
    rendered = build_html(panel)
    assert "同尺度比较" in rendered
    assert "不能严谨地把本段涨跌归因" in rendered


def test_blind_replay_hides_dates_and_fills_next_daily_quote():
    rows = [
        {"observed_on": f"2026-01-{day:02d}", "price": 100 + day, "return_1d": 0}
        for day in range(1, 29)
    ]
    prompt = anonymised_prompt(rows[:20], in_position=False, rule=Decision("BUY", 0.7, "trend", "rule"))
    assert "2026-" not in prompt
    calls = iter((Decision("BUY", 0.9, "confirm", "test", "UP", 0.9), Decision("SELL", 0.9, "protect", "test", "DOWN", 0.9)))
    result = replay(rows, trade_start=date(2026, 1, 1), decision_provider=lambda *_args: next(calls, Decision("HOLD", 0, "", "test")))
    assert result["trades"][0]["signal_day"] == "2026-01-21"
    assert result["trades"][0]["fill_day"] == "2026-01-22"
    assert result["fees_paid"] > 0
    assert result["prediction_metrics"]["directional_calls"] > 0


def test_third_prior_month_is_a_complete_month():
    assert third_prior_month(date(2026, 9, 3)) == (date(2026, 6, 1), date(2026, 6, 30))


def test_blind_replay_uses_local_tool_without_sending_calendar_dates(monkeypatch):
    sent = {}

    class Response:
        def raise_for_status(self): pass
        def json(self): return {"action": "BUY", "confidence": 0.8, "reason": "trend"}

    def post(url, **kwargs):
        sent["url"], sent["json"] = url, kwargs["json"]
        return Response()

    monkeypatch.setattr("demos.gold_factor_lab.blind_replay.httpx.post", post)
    result = local_tool_decision(
        [{"observed_on": "2026-01-01", "price": 100, "return_1d": 0}], in_position=False,
        rule=Decision("BUY", 0.7, "trend", "rule"), tool_url="http://local/tool",
    )
    assert result.action == "BUY"
    assert sent["url"] == "http://local/tool"
    assert "2026-" not in str(sent["json"])


def test_sandbox_tool_only_accepts_the_blind_contract(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"action": "HOLD", "confidence": 0.4}

    monkeypatch.setattr("tools.deepseek_blind_gold_tool.httpx.post", lambda *_args, **_kwargs: Response())
    assert invoke({"position": "cash", "rule_candidate": "HOLD", "observations": [{"day": 1}]})["action"] == "HOLD"
    try:
        invoke({"position": "cash", "rule_candidate": "HOLD", "observations": [], "date": "2026-01-01"})
    except ValueError:
        pass
    else:
        raise AssertionError("the tool accepted a non-blind field")


def test_macro_context_uses_interpretable_factor_directions():
    history = [{"price": 100, "usd_cny": 7.0, "broad_us_dollar": 100, "us_10y_real_yield": 2.0, "wti_crude": 70}] * 5
    history.append({"price": 101, "usd_cny": 7.02, "broad_us_dollar": 99.5, "us_10y_real_yield": 1.95, "wti_crude": 71})
    score, available, oil_risk, _labels = _macro_context(history)
    assert (score, available, oil_risk) == (3, 3, False)


def test_daily_factor_values_are_delayed_one_calendar_row():
    rows = _daily_rows({
        "jd_zheshang_gold": [{"observed_on": "2026-01-01", "value": 100}, {"observed_on": "2026-01-02", "value": 101}],
        "usd_cny": [{"observed_on": "2026-01-01", "value": 7.1}],
    })
    assert rows[0]["usd_cny"] is None
    assert rows[1]["usd_cny"] == 7.1


def test_factor_calibration_marks_the_period_as_development_only():
    rows = []
    for day in range(1, 30):
        rows.append({"observed_on": f"2026-06-{day:02d}", "price": 100 + day, "usd_cny": 7 + day / 1000,
                     "broad_us_dollar": 100 - day / 100, "us_10y_real_yield": 2 - day / 100,
                     "wti_crude": 70, "return_1d": 0})
    result = calibrate(rows, start=date(2026, 6, 21), end=date(2026, 6, 28))
    assert result["development_only"] is True
    assert "keep_macro_support_gate" in result["decision"]


def test_evaluation_report_explains_flat_cash_is_not_profit():
    result = {"prediction_metrics": {"directional_accuracy_percent": 43.42, "up_call_precision_percent": 43.14}, "decisions": [{"signal_day": "2026-08-01", "rule": "BUY", "executed": "HOLD", "next_day_direction": "UP", "actual_next_day_direction": "DOWN"}], "return_percent": 0, "final_value": 100000, "buy_and_hold_return_percent": -2.794, "buy_and_hold_final_value": 97206.14, "trade_count": 0, "fees_paid": 0, "trades": [], "frozen_rule": {"entry": "test"}}
    page = build_evaluation_html(result, [{"observed_on": "2026-08-01", "value": 900}, {"observed_on": "2026-08-02", "value": 901}])
    assert "不是盈利" in page
    assert "京东浙商积存金价格" in page


def test_swing_replay_uses_partial_entry_and_sell_fee():
    rows = [{"observed_on": f"2026-01-{day:02d}", "price": 100 + day, "usd_cny": 7.1, "broad_us_dollar": 100,
             "us_10y_real_yield": 2, "wti_crude": 70} for day in range(1, 29)]
    result = swing_replay(rows, start=date(2026, 1, 21), end=date(2026, 1, 27), tool_url="unused", use_deepseek=False)
    assert any(item["action"] == "BUY_50" for item in result["trades"])
    assert result["terminal_exit_fee"] > 0
    assert result["final_value"] == round(result["mark_to_market_value"] - result["terminal_exit_fee"], 2)
    assert result["fees_paid"] >= result["terminal_exit_fee"]


def _v3_rows(prices: list[float]) -> list[dict]:
    first_day = date(2026, 1, 1)
    return [
        {
            "observed_on": (first_day + timedelta(days=index)).isoformat(),
            "price": price,
            "return_1d": 0.0 if index == 0 else price / prices[index - 1] - 1,
        }
        for index, price in enumerate(prices)
    ]


def test_swing_v3_accrues_terminal_sell_fee_and_uses_next_quote():
    rows = _v3_rows([100 * 1.002 ** index for index in range(135)])
    result = swing_v3_replay(rows, start=date.fromisoformat(rows[120]["observed_on"]), end=date.fromisoformat(rows[127]["observed_on"]), tool_url="unused", use_deepseek=False)
    assert result["trades"][0]["action"] == "BUY_SATELLITE"
    assert any(item["action"] == "BUY_SATELLITE" for item in result["trades"])
    assert not any(item["action"] == "BUY_CORE" for item in result["trades"])
    assert result["trades"][0]["signal_day"] == rows[120]["observed_on"]
    assert result["trades"][0]["fill_day"] == rows[121]["observed_on"]
    assert result["terminal_exit_fee"] > 0
    assert result["final_value"] == round(result["mark_to_market_value"] - result["terminal_exit_fee"], 2)
    assert result["fees_paid"] >= result["terminal_exit_fee"]


def test_swing_v3_features_and_model_window_do_not_use_future_rows():
    rows = _v3_rows([100 * 1.0015 ** index for index in range(135)])
    before = swing_v3_features(rows[:121])
    observations = _standardised_observations(rows[:121])
    for row in rows[121:]:
        row["price"] *= 10
    after = swing_v3_features(rows[:121])
    assert before == after
    assert "2026-" not in str(observations)
    assert "gold_cny_per_gram" not in str(observations)


def test_swing_v3_sells_satellite_without_forcing_core_exit():
    rising = [100 * 1.002 ** index for index in range(126)]
    prices = rising + [rising[-1] * 0.98, rising[-1] * 0.979, rising[-1] * 0.978, rising[-1] * 0.977]
    rows = _v3_rows(prices)
    result = swing_v3_replay(rows, start=date.fromisoformat(rows[120]["observed_on"]), end=date.fromisoformat(rows[128]["observed_on"]), tool_url="unused", use_deepseek=False)
    assert any(item["action"] == "SELL_SATELLITE" for item in result["trades"])
    assert not any(item["action"] == "SELL_CORE" for item in result["trades"])
    assert result["open_core_grams"] == 0


def test_swing_v3_promotes_existing_value_inventory_to_core_without_buying_high():
    state = PositionState(cash=70_000, value_grams=300)
    grams, notional = _promote_value_to_core(state, price=100, target_weight=0.25)
    assert (grams, notional) == (250, 25_000)
    assert state.core_grams == 250
    assert state.value_grams == 50
    assert state.cash == 70_000


def test_swing_v3_core_trim_keeps_half_the_position_and_records_fee():
    state = PositionState(cash=0, core_grams=100)
    notional, fee = _trim_core(state, price=100, fraction=0.5, sell_fee=0.004)
    assert (notional, fee) == (5_000, 20)
    assert state.core_grams == 50
    assert state.cash == 4_980
    assert state.core_trimmed is True


def test_swing_v3_value_layer_enters_a_stabilised_discount_without_price_constants():
    prices = [100.0] * 120 + [98.0, 97.0, 97.2, 97.3, 97.4]
    rows = _v3_rows(prices)
    result = swing_v3_replay(rows, start=date.fromisoformat(rows[122]["observed_on"]), end=date.fromisoformat(rows[123]["observed_on"]), tool_url="unused", use_deepseek=False)
    value_trade = next(item for item in result["trades"] if item["action"] == "BUY_VALUE")
    assert value_trade["price"] == rows[123]["price"]
    assert value_trade["reason"] == "VALUATION_DISCOUNT_STABILISED"


def test_blind_gold_v3_context_forbids_dates_and_unknown_fields(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": '{"action":"HOLD","confidence":0.4,"risk_severity":"mild","reason_codes":["RESISTANCE_TOO_CLOSE"]}'}}]}

    sent = {}
    monkeypatch.setattr("src.quant_research.intelligence._read_key", lambda: "test-key")
    monkeypatch.setattr("src.quant_research.intelligence.httpx.post", lambda *_args, **kwargs: sent.update(kwargs) or Response())
    context = BlindGoldAnalysisContext(
        sequence=61, candidate_stage="entry", requested_horizon_sessions=[5, 10], current_weight_pct=0,
        held_sessions=0, buy_fee_pct=0, sell_fee_pct=0.4, max_factor_age_sessions=1, macro_score=1, macro_available=3,
    ).model_dump()
    result = analyse_blind_gold([{"day": 1, "gold_index_base100": 100, "gold_return_1d_pct": 0.1}], "cash", "HOLD", "trade_quality", context)
    assert result["risk_severity"] == "mild"
    assert result["reason_codes"] == ["RESISTANCE_TOO_CLOSE"]
    assert "2026-" not in str(sent["json"])
    try:
        analyse_blind_gold([{"day": 1}], "cash", "HOLD", "trade_quality", {**context, "observed_on": "2026-01-01"})
    except ValueError:
        pass
    else:
        raise AssertionError("the feature-only context accepted a calendar field")
