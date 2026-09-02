from datetime import date

import pytest

from src.quant_research.models import Quote, StockItem
from src.quant_research.stock_service import StockService, normalize_symbol
from src.quant_research.cache_store import same_refresh_window
from src.quant_research.fund_data import _ensure_short_returns


class FakeProvider:
    def list_stocks(self):
        return [StockItem(symbol="600000", exchange="SH", name="浦发银行")]

    def get_quote(self, symbol):
        return Quote(symbol=symbol, exchange="SH", name="浦发银行", price=10.0)

    def get_history(self, symbol, start, end, period, adjust):
        return []


@pytest.mark.parametrize("raw", ["600000", "sh600000", "SH.600000", "sh:600000"])
def test_normalize_symbol(raw):
    assert normalize_symbol(raw) == "600000"


def test_invalid_symbol():
    with pytest.raises(ValueError):
        normalize_symbol("abc")


def test_search():
    assert StockService(FakeProvider()).stocks("浦发")[0].symbol == "600000"


def test_fuzzy_search_by_category_word_in_name():
    service = StockService(FakeProvider())
    assert service.stocks("银行")[0].name == "浦发银行"


def test_market_cache_uses_aligned_ten_minute_windows():
    assert same_refresh_window(10 * 3600 + 1, 10 * 3600 + 599, 600)
    assert not same_refresh_window(10 * 3600 + 599, 10 * 3600 + 600, 600)


def test_fund_latest_change_is_derived_from_previous_nav():
    result = _ensure_short_returns({"latest": {"nav": 1.2, "daily_return": None}, "returns": {},
                                    "history": [{"date": "2026-01-01", "nav": 1.0},
                                                {"date": "2026-01-02", "nav": 1.2}]})
    assert result["latest"]["previous_nav"] == 1.0
    assert result["latest"]["nav_change"] == 0.2
    assert result["latest"]["daily_return"] == 20.0
