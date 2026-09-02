"""Descriptive analysis for the standalone gold-factor lab; no trade signals."""
from __future__ import annotations

from math import sqrt


def _returns(values: list[float]) -> list[float | None]:
    return [None] + [values[index] / values[index - 1] - 1 for index in range(1, len(values))]


def _max_drawdown(values: list[float]) -> float:
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 8 or len(left) != len(right):
        return None
    left_mean, right_mean = sum(left) / len(left), sum(right) / len(right)
    left_var = sum((item - left_mean) ** 2 for item in left)
    right_var = sum((item - right_mean) ** 2 for item in right)
    if not left_var or not right_var:
        return None
    return sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right)) / sqrt(left_var * right_var)


def describe(panel: dict[str, list[dict]]) -> dict:
    """Return only descriptive, contemporaneous diagnostics for the demo."""
    gold = sorted(panel["jd_zheshang_gold"], key=lambda item: item["observed_on"])
    gold_values = [float(item["value"]) for item in gold]
    gold_dates = [item["observed_on"] for item in gold]
    gold_return = _returns(gold_values)
    correlations = {}
    for name, rows in panel.items():
        if name == "jd_zheshang_gold":
            continue
        by_date = {item["observed_on"]: float(item["value"]) for item in rows}
        aligned = [(index, by_date[day]) for index, day in enumerate(gold_dates) if day in by_date]
        factor_values = [value for _, value in aligned]
        factor_returns = _returns(factor_values)
        paired = [(gold_return[index], factor_returns[offset]) for offset, (index, _) in enumerate(aligned)
                  if gold_return[index] is not None and factor_returns[offset] is not None]
        correlations[name] = {
            "matched_days": len(aligned),
            "return_pairs": len(paired),
            "contemporaneous_return_correlation": _pearson(
                [pair[0] for pair in paired], [pair[1] for pair in paired],
            ),
        }
    return {
        "scope": "descriptive_only_not_a_forecast_or_trading_rule",
        "gold": {
            "rows": len(gold), "start": gold_dates[0], "end": gold_dates[-1],
            "start_price_cny_per_gram": gold_values[0], "end_price_cny_per_gram": gold_values[-1],
            "return_percent": round((gold_values[-1] / gold_values[0] - 1) * 100, 3),
            "max_drawdown_percent": round(_max_drawdown(gold_values) * 100, 3),
        },
        "factor_diagnostics": correlations,
        "limitations": [
            "One month is adequate for a pipeline demo, not for estimating stable factor effects.",
            "Correlations are contemporaneous and descriptive; they do not establish causality or predict returns.",
            "Raw history is strict: it is only known at collection time. A backtest needs a separately approved release-time contract.",
        ],
    }
