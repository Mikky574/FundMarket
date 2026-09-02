from datetime import datetime, timezone

from demos.gold_factor_lab.analysis import describe
from demos.gold_factor_lab import collector


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
