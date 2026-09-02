"""Explainable, read-only quantitative research helpers.

The service intentionally produces observations and risk signals, never trading
instructions.  It is used by the public read-only API and the QQ research
assistant so that both start from the same dated evidence.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from math import sqrt
from statistics import pstdev

from src.quant_research.fund_data import get_fund_overview


DATA_SOURCE = "AkShare / Eastmoney public fund NAV"


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _latest_weekday(today: date) -> str:
    """Weekend-safe freshness guard; public holidays still need an exchange calendar."""
    offset = 1 if today.weekday() == 5 else 2 if today.weekday() == 6 else 0
    return date.fromordinal(today.toordinal() - offset).isoformat()


def _returns(values: list[float]) -> list[float]:
    return [(current / previous - 1) * 100 for previous, current in zip(values, values[1:]) if previous]


def _moving_average(values: list[float], window: int) -> float | None:
    return sum(values[-window:]) / window if len(values) >= window else None


def _rsi14(values: list[float]) -> float | None:
    changes = [current - previous for previous, current in zip(values, values[1:])][-14:]
    if len(changes) < 14:
        return None
    gains = sum(change for change in changes if change > 0) / 14
    losses = -sum(change for change in changes if change < 0) / 14
    if not losses:
        return 100.0 if gains else 50.0
    return 100 - 100 / (1 + gains / losses)


def compute_fund_signals(overview: dict) -> dict:
    """Calculate reproducible trend and risk signals from disclosed NAV history."""
    history = overview.get("history", [])
    navs = [float(row["nav"]) for row in history if row.get("nav") is not None]
    latest = overview.get("latest", {})
    latest_nav = float(latest["nav"]) if latest.get("nav") is not None else None
    ma20, ma60 = _moving_average(navs, 20), _moving_average(navs, 60)
    daily = _returns(navs[-61:])
    volatility = pstdev(daily) * sqrt(252) if len(daily) >= 20 else None
    rsi = _rsi14(navs)
    trend_points = 0
    if latest_nav is not None and ma20 is not None:
        trend_points += 35 if latest_nav >= ma20 else 0
    if ma20 is not None and ma60 is not None:
        trend_points += 35 if ma20 >= ma60 else 0
    if overview.get("returns", {}).get("one_month") is not None:
        trend_points += 30 if overview["returns"]["one_month"] >= 0 else 0
    trend_label = "上行" if trend_points >= 70 else "震荡" if trend_points >= 35 else "偏弱"
    latest_date = latest.get("date")
    return {
        "code": overview.get("code"),
        "name": overview.get("name"),
        "data_as_of": latest_date,
        "latest_nav": latest_nav,
        "returns_percent": {
            "one_week": overview.get("returns", {}).get("one_week"),
            "one_month": overview.get("returns", {}).get("one_month"),
            "three_months": overview.get("returns", {}).get("three_months"),
            "one_year": overview.get("returns", {}).get("one_year"),
        },
        "trend": {
            "label": trend_label,
            "score_0_100": trend_points,
            "ma20": _round(ma20, 4),
            "ma60": _round(ma60, 4),
            "rsi14": _round(rsi),
        },
        "risk": {
            "annualized_volatility_percent": _round(volatility),
            "max_drawdown_one_year_percent": overview.get("max_drawdown_one_year"),
        },
        "limitations": [
            "信号基于已披露的基金单位净值，不是盘中估值或收益预测。",
            "趋势、波动和 RSI 仅用于辅助比较，不能单独作为买卖依据。",
        ],
    }


def market_brief(codes: list[str], force_refresh: bool = False) -> dict:
    """Return a dated, source-labelled research packet for one or more funds."""
    clean_codes = []
    for code in codes:
        code = str(code).strip()
        if code.isdigit() and len(code) == 6 and code not in clean_codes:
            clean_codes.append(code)
    if not clean_codes:
        raise ValueError("至少提供一个 6 位基金代码")
    if len(clean_codes) > 12:
        raise ValueError("一次最多研究 12 只基金")

    signals, unavailable = [], []
    for code in clean_codes:
        try:
            signals.append(compute_fund_signals(get_fund_overview(code, force_refresh=force_refresh)))
        except Exception as exc:  # Preserve other funds and make data gaps explicit.
            unavailable.append({"code": code, "reason": str(exc)[:180]})
    dates = [item["data_as_of"] for item in signals if item.get("data_as_of")]
    newest = max(dates) if dates else None
    expected_trading_day = _latest_weekday(date.today())
    return {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "data_source": {"name": DATA_SOURCE, "mode": "public_disclosed_nav", "data_as_of": newest,
                        "expected_latest_trading_day": expected_trading_day,
                        "stale": bool(newest and newest < expected_trading_day),
                        "note": "基金净值通常在交易日收盘后披露；周末不等同于数据故障，法定休市日将在交易日历接入后精确识别。"},
        "signals": signals,
        "unavailable": unavailable,
        "research_scope": [
            "已实现：净值趋势、动量、波动、最大回撤与数据日期核验。",
            "待接入：交易所指数、宏观、基金公告和新闻事件；这些数据需保留来源链接与发布时间。",
        ],
    }
