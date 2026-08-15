"""Deterministic fund-position research; no model calls and no trade decisions."""
from __future__ import annotations

from math import sqrt
from statistics import pstdev


# These mappings are deliberately explicit.  A fund not listed here is not
# guessed from its name and remains unmapped until a fund-contract source is
# recorded in paper/fund_pool.json.
FUND_MAPPINGS = {
    "018099": {"industries": ["保险"], "confidence": "high", "basis": "基金名称与跟踪主题明确"},
    "007467": {"industries": [], "confidence": "high", "basis": "中证红利低波指数基金；防御风格，不映射单一行业"},
    "008280": {"industries": ["煤炭行业"], "confidence": "high", "basis": "基金名称与跟踪主题明确"},
    "008021": {"industries": ["软件开发", "通信设备"], "confidence": "medium", "basis": "宽主题指数，需以基金合同补充细分权重"},
    "011609": {"industries": ["半导体", "软件开发", "通信设备"], "confidence": "medium", "basis": "科创50宽主题指数，需以基金合同补充细分权重"},
    "007301": {"industries": ["半导体"], "confidence": "high", "basis": "基金名称与跟踪主题明确"},
    "014881": {"industries": ["软件开发", "自动化设备"], "confidence": "medium", "basis": "宽主题指数，需以基金合同补充细分权重"},
    "013416": {"industries": ["医疗器械"], "confidence": "high", "basis": "基金名称与跟踪主题明确"},
    "012738": {"industries": ["医疗服务", "化学制药", "生物制品"], "confidence": "medium", "basis": "宽主题指数，需以基金合同补充细分权重"},
    "016129": {"industries": [], "confidence": "high", "basis": "红利低波指数基金；防御风格，不映射单一行业"},
}


def _ma(values: list[float], window: int) -> float | None:
    return sum(values[-window:]) / window if len(values) >= window else None


def _rsi(values: list[float]) -> float | None:
    changes = [b - a for a, b in zip(values, values[1:])][-14:]
    if len(changes) < 14:
        return None
    gain = sum(x for x in changes if x > 0) / 14
    loss = -sum(x for x in changes if x < 0) / 14
    return 100.0 if not loss and gain else 50.0 if not loss else 100 - 100 / (1 + gain / loss)


def fund_research_card(overview: dict, industry_rows: list[dict]) -> dict:
    """Classify a fund as recovery, continuation, pullback, hot or weak.

    States describe current position relative to its own disclosed NAV history;
    they are not forecasts and cannot create an order.
    """
    code = str(overview.get("code", ""))
    mapping = FUND_MAPPINGS.get(code, {"industries": [], "confidence": "none", "basis": "未记录可核验的行业/指数映射"})
    navs = [float(item["nav"]) for item in overview.get("history", []) if item.get("nav") is not None]
    if len(navs) < 60:
        return {"fund_code": code, "fund_name": overview.get("name"), "state": "INSUFFICIENT_DATA", "mapping": mapping}
    nav, ma20, ma60 = navs[-1], _ma(navs, 20), _ma(navs, 60)
    r5 = (nav / navs[-6] - 1) * 100 if len(navs) >= 6 else None
    r20 = (nav / navs[-21] - 1) * 100 if len(navs) >= 21 else None
    high60 = max(navs[-60:]); distance_high = (nav / high60 - 1) * 100
    bias20 = (nav / ma20 - 1) * 100 if ma20 else None
    rsi = _rsi(navs)
    daily = [(b / a - 1) * 100 for a, b in zip(navs[-21:], navs[-20:]) if a]
    volatility = pstdev(daily) * sqrt(252) if len(daily) >= 14 else None
    industry = {row.get("name"): row for row in industry_rows}
    mapped = [industry[name] for name in mapping["industries"] if name in industry]
    industry_relative = None
    if mapped and r20 is not None:
        industry_return = sum(float(row.get("return_20d") or 0) for row in mapped) / len(mapped)
        industry_relative = round(r20 - industry_return, 2)
    overheated = bool((bias20 is not None and bias20 >= 7) or (rsi is not None and rsi >= 72) or (r5 is not None and r5 >= 8))
    if overheated:
        state = "OVERHEATED"
    elif nav > ma20 > ma60:
        state = "TREND_CONTINUATION"
    elif nav >= ma20 and ma20 <= ma60:
        state = "EARLY_RECOVERY"
    elif nav < ma20 and ma20 > ma60 and distance_high >= -10:
        state = "PULLBACK_IN_UPTREND"
    else:
        state = "WEAK_OR_BREAKDOWN"
    confirmation = ["基金净值保持在 MA20 上方"]
    invalidation = ["基金净值跌破 MA20 且 20 日相对收益继续转弱"]
    if mapping["confidence"] in {"high", "medium"}:
        confirmation.append("对应行业机会候选未失效")
        invalidation.append("对应行业趋势或相对强弱失效")
    return {"fund_code": code, "fund_name": overview.get("name"), "state": state, "mapping": mapping,
            "metrics": {"nav": round(nav, 4), "ma20": round(ma20, 4), "ma60": round(ma60, 4),
                        "nav_vs_ma20_percent": round(bias20, 2), "return_5d_percent": round(r5, 2) if r5 is not None else None,
                        "return_20d_percent": round(r20, 2) if r20 is not None else None,
                        "rsi14": round(rsi, 1) if rsi is not None else None,
                        "distance_to_60d_high_percent": round(distance_high, 2),
                        "annualized_volatility_percent": round(volatility, 2) if volatility is not None else None,
                        "relative_to_mapped_industry_20d_percent": industry_relative},
            "confirmation_conditions": confirmation, "invalidation_conditions": invalidation,
            "risk_flags": (["短期偏离、RSI 或短线涨幅显示偏热，不作为追高依据"] if overheated else [])}
