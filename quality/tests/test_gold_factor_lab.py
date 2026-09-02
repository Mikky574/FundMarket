from datetime import date, datetime, timezone

from demos.gold_factor_lab.analysis import describe
from demos.gold_factor_lab import collector
from demos.gold_factor_lab.history_chart import build_html
from demos.gold_factor_lab.blind_replay import Decision, anonymised_prompt, local_tool_decision, replay, third_prior_month
from tools.deepseek_blind_gold_tool import invoke


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
    calls = iter((Decision("BUY", 0.9, "confirm", "test"), Decision("SELL", 0.9, "protect", "test")))
    result = replay(rows, trade_start=date(2026, 1, 1), decision_provider=lambda *_args: next(calls, Decision("HOLD", 0, "", "test")))
    assert result["trades"][0]["signal_day"] == "2026-01-21"
    assert result["trades"][0]["fill_day"] == "2026-01-22"
    assert result["fees_paid"] > 0


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
