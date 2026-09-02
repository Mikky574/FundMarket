from datetime import datetime, timezone

from demos.gold_factor_lab.analysis import describe
from demos.gold_factor_lab import collector
from demos.gold_factor_lab.history_chart import build_html


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
