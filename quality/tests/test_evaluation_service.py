from pathlib import Path

import pytest

from src.historical_evaluation import service as evaluation_service


@pytest.fixture(autouse=True)
def evaluation_root(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluation_service.settings, "evaluation_data_root", str(tmp_path / "evaluation"))


def market(as_of: str, close: str, available_at: str) -> dict:
    return {"instrument": "018099", "as_of": as_of, "close": close,
            "available_at": available_at, "source": "test"}


def test_session_hides_future_market_and_news():
    evaluation_service.import_market([
        market("2026-01-02", "1.00", "2026-01-02T20:00:00+08:00"),
        market("2026-01-03", "1.10", "2026-01-03T20:00:00+08:00"),
    ])
    evaluation_service.import_news([{"source": "official", "title": "known", "body": "content",
                                      "available_at": "2026-01-02T10:00:00+08:00"},
                                     {"source": "official", "title": "future", "body": "content",
                                      "available_at": "2026-01-03T10:00:00+08:00"}])
    session = evaluation_service.create_session("2026-01-02", ["018099"])
    assert [row["as_of"] for row in session["market"]] == ["2026-01-02"]
    assert [row["title"] for row in session["news"]] == ["known"]


def test_undated_news_and_conflicting_history_are_rejected():
    with pytest.raises(ValueError, match="available_at"):
        evaluation_service.import_news([{"source": "x", "title": "x", "body": "x"}])
    row = market("2026-01-02", "1.00", "2026-01-02T20:00:00+08:00")
    evaluation_service.import_market([row])
    with pytest.raises(ValueError, match="immutable"):
        evaluation_service.import_market([{**row, "close": "2.00"}])


def test_prediction_is_scored_only_after_future_rows_exist():
    evaluation_service.import_market([
        market("2026-01-02", "1.00", "2026-01-02T20:00:00+08:00"),
        market("2026-01-03", "1.10", "2026-01-03T20:00:00+08:00"),
        market("2026-01-04", "1.20", "2026-01-04T20:00:00+08:00"),
    ])
    session = evaluation_service.create_session("2026-01-02", ["018099"])
    prediction = evaluation_service.record_prediction(session["session_id"], {
        "instrument": "018099", "direction": "UP", "confidence": 70, "horizon_trading_days": 2,
    })
    result = evaluation_service.score(session["session_id"])
    assert result["results"] == [{"prediction_id": prediction["prediction_id"], "status": "SCORED",
                                   "target_as_of": "2026-01-04", "return_percent": 20.0,
                                   "actual_direction": "UP", "direction_hit": True}]
    assert result["metrics"]["direction_hit_rate"] == 1.0
