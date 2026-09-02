import sqlite3
from datetime import datetime, timezone

from demos.gold_factor_lab import live_task


def test_live_task_records_latest_and_intraday_without_production_state(monkeypatch, tmp_path):
    moment = datetime(2026, 9, 3, 10, tzinfo=timezone.utc).isoformat()
    monkeypatch.setattr(live_task, "collect_jd_latest", lambda: {
        "source": "jd_zheshang_latest", "source_at": moment, "retrieved_at": moment, "value": 945.43,
        "unit": "cny_per_gram", "availability_basis": "observed_live_quote", "up_and_down_rate": "0.01%",
    })
    monkeypatch.setattr(live_task, "collect_jd_intraday", lambda: [{
        "source": "jd_zheshang_public_intraday_chart", "source_at": moment, "retrieved_at": moment,
        "value": 945.43, "unit": "cny_per_gram", "availability_basis": "observed_live_quote",
    }])
    database = tmp_path / "gold.sqlite3"
    with live_task._connect(database) as connection:
        result = live_task.collect_once(connection)
    with sqlite3.connect(database) as connection:
        count = connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    assert result["errors"] == []
    assert count == 2
