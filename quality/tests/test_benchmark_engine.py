from decimal import Decimal

from src.historical_evaluation.benchmark import calculate_record


def test_benchmark_and_excess_returns():
    result = calculate_record("2026-07-23", {"csi300": 100, "industry": 100, "fund_nav": 1},
                              {"csi300": 102, "industry": 105, "fund_nav": 1.04}, Decimal("1"))
    assert result["returns_percent"]["cash_50_csi300_50"] == "1.0000"
    assert result["returns_percent"]["industry"] == "5.0000"
    assert result["excess_percent"]["vs_cash_csi300"] == "0.0000"
    assert result["excess_percent"]["vs_fund_buy_hold"] == "-3.0000"
