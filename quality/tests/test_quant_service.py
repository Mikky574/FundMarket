from app.quant_service import compute_fund_signals


def test_compute_fund_signals_has_explainable_metrics():
    history = [{"date": f"2026-01-{day:02d}", "nav": 1 + day * 0.01} for day in range(1, 62)]
    overview = {
        "code": "000001", "name": "测试基金", "latest": history[-1], "history": history,
        "returns": {"one_week": 4.0, "one_month": 15.0, "three_months": None, "one_year": None},
        "max_drawdown_one_year": -2.5,
    }
    result = compute_fund_signals(overview)
    assert result["trend"]["label"] == "上行"
    assert result["trend"]["ma20"] is not None
    assert result["risk"]["annualized_volatility_percent"] is not None
    assert result["data_as_of"] == "2026-01-61"
