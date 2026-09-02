from datetime import datetime, timedelta, timezone

import pytest

from src.qq_control import draft_research as public_ai_research
from src.quant_research.intelligence import collect_news
from src.quant_research.intelligence import opportunity_candidates
from src.quant_research.intelligence import refresh_public_market_display
from src.quant_research import gold_prices
from src.quant_research.fund_signals import fund_research_card


def test_validate_draft_forces_unexecuted_confirmation():
    result = public_ai_research._validate_draft({"action": "WATCH", "confidence": 42})
    assert result["requires_user_confirmation"] is True
    assert result["execution_status"] == "draft_only"


def test_validate_draft_rejects_invalid_action():
    with pytest.raises(RuntimeError):
        public_ai_research._validate_draft({"action": "EXECUTE", "confidence": 42})


def test_public_evidence_fails_closed_when_stale(monkeypatch):
    stale = (datetime.now(timezone.utc) - timedelta(minutes=100)).astimezone().isoformat()
    monkeypatch.setattr(public_ai_research, "latest", lambda: {"available": True, "generated_at": stale})
    with pytest.raises(RuntimeError, match="过期"):
        public_ai_research.public_evidence_packet()


def test_collect_news_drops_old_and_undated_items(monkeypatch):
    xml = b"""<rss><channel>
      <item><title>recent</title><pubDate>Tue, 11 Aug 2026 10:00:00 +0800</pubDate><link>https://x/recent</link></item>
      <item><title>old</title><pubDate>Mon, 01 Jan 2024 10:00:00 +0800</pubDate><link>https://x/old</link></item>
      <item><title>undated</title><link>https://x/undated</link></item>
    </channel></rss>"""

    class Response:
        def read(self): return xml
        def __enter__(self): return self
        def __exit__(self, *args): return False

    monkeypatch.setattr("src.quant_research.intelligence.settings.market_news_rss_urls", "https://x/rss")
    monkeypatch.setattr("src.quant_research.intelligence.urlopen", lambda *_args, **_kwargs: Response())
    now = datetime(2026, 8, 12, 10, tzinfo=timezone.utc)
    assert [item["title"] for item in collect_news(now)] == ["recent"]


def test_opportunity_candidates_require_observable_confirmation():
    quant = {"benchmark": {"return_20d": 0}, "industries": [{
        "name": "测试行业", "return_5d": 2, "return_20d": 8, "volume_ratio_20d": 1.2,
        "data_confidence": 80, "scores": {"trend": 80, "relative_strength": 75, "risk_control": 70},
        "signals": {"trend_state": "uptrend", "relative_return_20d": 8, "breadth_percent": 75,
                    "volatility_20d": 20, "drawdown_60d": -5},
    }]}
    result = opportunity_candidates(quant)
    assert result[0]["industry"] == "测试行业"
    assert result[0]["confirmation_conditions"]
    assert result[0]["invalidation_conditions"]


def test_market_refresh_does_not_call_deepseek_when_realtime_is_disabled(monkeypatch):
    monkeypatch.setattr("src.quant_research.intelligence.settings.deepseek_realtime_enabled", False)
    monkeypatch.setattr(
        "src.quant_research.intelligence.refresh_quant_snapshot",
        lambda: {"snapshot_at": "2026-09-03T10:00:00+08:00", "data_through": "2026-09-03", "candidate_count": 2},
    )
    monkeypatch.setattr(
        "src.quant_research.intelligence.refresh_intelligence",
        lambda _quant: pytest.fail("DeepSeek must not run during a normal market refresh"),
    )

    result = refresh_public_market_display()

    assert result["research_summary_refreshed"] is False
    assert result["research_summary_skipped"] == "DeepSeek realtime refresh is disabled"


def test_gold_history_keeps_retrieval_time_in_strict_mode(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"success": True, "resultData": {"data": {"line": [
                {"date": "20260803", "price": "879.46", "raisePercent": "0.0000"},
            ]}}}

    monkeypatch.setattr(gold_prices.httpx, "post", lambda *_args, **_kwargs: Response())
    received = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)

    rows = gold_prices.fetch_one_month_history(retrieved_at=received)

    assert rows[0]["as_of"] == "2026-08-03"
    assert rows[0]["available_at"] == received.isoformat()
    assert rows[0]["availability_basis"] == "retrieved_after_the_fact"


def test_fund_research_marks_hot_fund_without_calling_it_a_buy_signal():
    history = [{"nav": 1 + day * 0.01} for day in range(60)]
    result = fund_research_card({"code": "018099", "name": "保险基金", "history": history}, [])
    assert result["state"] == "OVERHEATED"
    assert result["risk_flags"]
