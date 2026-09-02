from cachetools import TTLCache
import akshare as ak
import pandas as pd
from src.config import settings
from src.quant_research.cache_store import (get_aligned_cache, get_aligned_caches_by_prefix, get_cache,
                              put_cache, record_search)


_cache = TTLCache(maxsize=128, ttl=settings.cache_ttl_seconds)
KNOWN_NAMES = {
    "001407": "景顺长城稳健回报混合C",
    "018099": "方正富邦中证保险C",
    "025491": "平安中证卫星产业指数C",
}


def _mark_catalog(rows: list[dict]) -> list[dict]:
    for item in rows:
        item["purchasable"] = "后端" not in item.get("name", "")
    return rows


def _fund_statuses() -> dict[str, dict]:
    # 这是申购/净值可用性目录，不是实时净值；按天刷新即可。
    cached, fresh = get_cache("funds:daily_status", 24 * 60 * 60)
    if cached is not None and fresh:
        return cached
    try:
        frame = ak.fund_open_fund_daily_em()
        code_col = next((c for c in frame.columns if "基金代码" in str(c)), frame.columns[0])
        name_col = next((c for c in frame.columns if "基金简称" in str(c)), frame.columns[1])
        purchase_col = next((c for c in frame.columns if "申购状态" in str(c)), None)
        nav_cols = [c for c in frame.columns if "单位净值" in str(c)]
        statuses = {}
        for _, row in frame.iterrows():
            code = str(row[code_col]).zfill(6)
            nav_available = any(str(row.get(column, "")).strip() not in {"", "nan", "None"} for column in nav_cols)
            purchase_status = str(row[purchase_col]) if purchase_col else "未知"
            statuses[code] = {"name": str(row[name_col]), "purchase_status": purchase_status,
                              "previewable": nav_available,
                              "purchasable": nav_available and not any(x in purchase_status for x in ("暂停", "停止", "封闭"))}
        put_cache("funds:daily_status", "fund_daily_status", statuses)
        return statuses
    except Exception:
        return cached or {}


def _enrich_search_rows(rows: list[dict]) -> list[dict]:
    statuses = _fund_statuses()
    overrides = get_aligned_caches_by_prefix("fund:availability:", settings.market_refresh_seconds)
    result = []
    for item in rows:
        status = statuses.get(item["code"])
        override = overrides.get(f"fund:availability:{item['code']}")
        item["previewable"] = bool(status and status["previewable"])
        item["purchase_status"] = status["purchase_status"] if status else "暂无净值状态"
        item["purchasable"] = bool(item.get("purchasable") and status and status["purchasable"])
        if override is not None:
            item["previewable"] = bool(override.get("available"))
            item["purchasable"] = bool(item["purchasable"] and item["previewable"])
        result.append(item)
    return result


def search_funds(query: str, limit: int = 20) -> list[dict]:
    query = query.strip()
    if not query:
        return []
    record_search("fund", query)
    cached, fresh = get_cache("funds:catalog", 24 * 60 * 60)
    if cached is not None and fresh:
        rows = [x for x in _mark_catalog(cached) if query.lower() in x["code"].lower() or query.lower() in x["name"].lower()]
        return _enrich_search_rows(rows[:limit])
    try:
        frame = ak.fund_name_em()
    except Exception:
        if cached is None:
            raise
        rows = [x for x in _mark_catalog(cached) if query.lower() in x["code"].lower() or query.lower() in x["name"].lower()]
        return _enrich_search_rows(rows[:limit])
    code_col = next((c for c in frame.columns if "基金代码" in str(c)), frame.columns[0])
    name_col = next((c for c in frame.columns if "基金简称" in str(c)), frame.columns[1])
    type_col = next((c for c in frame.columns if "基金类型" in str(c)), None)
    mask = frame[code_col].astype(str).str.contains(query, case=False, na=False) | frame[name_col].astype(str).str.contains(query, case=False, na=False)
    all_rows = _mark_catalog([{"code": str(row[code_col]).zfill(6), "name": str(row[name_col]), "type": str(row[type_col]) if type_col else "基金"} for _, row in frame.iterrows()])
    put_cache("funds:catalog", "fund_catalog", all_rows)
    return _enrich_search_rows([x for x in all_rows if query.lower() in x["code"].lower() or query.lower() in x["name"].lower()][:limit])


def _return_at(values: list[dict], days: int) -> float | None:
    if len(values) < 2:
        return None
    end_date = pd.Timestamp(values[-1]["date"])
    candidates = [x for x in values if pd.Timestamp(x["date"]) <= end_date - pd.Timedelta(days=days)]
    if not candidates or not candidates[-1]["nav"]:
        return None
    return round((values[-1]["nav"] / candidates[-1]["nav"] - 1) * 100, 2)


def _ensure_short_returns(result: dict) -> dict:
    result.setdefault("returns", {})["one_week"] = _return_at(result.get("history", []), 7)
    history = result.get("history", [])
    if history:
        latest = result.setdefault("latest", history[-1])
        previous_nav = history[-2]["nav"] if len(history) > 1 else history[-1]["nav"]
        latest["previous_nav"] = previous_nav
        latest["nav_change"] = round(latest["nav"] - previous_nav, 4)
        if latest.get("daily_return") is None:
            latest["daily_return"] = round((latest["nav"] / previous_nav - 1) * 100, 2) if previous_nav else None
    return result


def get_fund_overview(code: str, force_refresh: bool = False) -> dict:
    if not code.isdigit() or len(code) != 6:
        raise ValueError("基金代码应为 6 位数字")
    if force_refresh:
        _cache.pop(code, None)
    if code in _cache:
        return _ensure_short_returns(_cache[code])
    disk_key = f"fund:overview:{code}"
    cached, fresh = get_aligned_cache(disk_key, settings.market_refresh_seconds)
    if cached is not None and fresh and not force_refresh:
        cached = _ensure_short_returns(cached)
        _cache[code] = cached
        return cached
    try:
        frame = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
    except Exception:
        if cached is None:
            put_cache(f"fund:availability:{code}", "fund_availability", {"available": False})
            raise
        cached = _ensure_short_returns(cached)
        _cache[code] = cached
        return cached
    if frame.empty:
        put_cache(f"fund:availability:{code}", "fund_availability", {"available": False})
        raise LookupError(f"未找到基金 {code}")
    points = [
        {"date": pd.Timestamp(row["净值日期"]).date().isoformat(), "nav": float(row["单位净值"]),
         "daily_return": None if pd.isna(row.get("日增长率")) else float(row["日增长率"])}
        for _, row in frame.iterrows()
    ]
    one_year = points[-250:]
    peak, max_drawdown = one_year[0]["nav"], 0.0
    for point in one_year:
        peak = max(peak, point["nav"])
        max_drawdown = min(max_drawdown, (point["nav"] / peak - 1) * 100)
    name = KNOWN_NAMES.get(code)
    fund_type = "基金"
    if not name:
        try:
            matches = search_funds(code, 1)
            if matches:
                name, fund_type = matches[0]["name"], matches[0]["type"]
        except Exception:
            name = None
    result = {
        "code": code,
        "name": name or f"基金 {code}",
        "type": fund_type,
        "latest": points[-1],
        "returns": {
            "one_week": _return_at(points, 7),
            "one_month": _return_at(points, 30),
            "three_months": _return_at(points, 90),
            "one_year": _return_at(points, 365),
            "since_inception": round((points[-1]["nav"] / points[0]["nav"] - 1) * 100, 2),
        },
        "max_drawdown_one_year": round(max_drawdown, 2),
        "history": points[-750:],
    }
    result = _ensure_short_returns(result)
    _cache[code] = result
    put_cache(f"fund:availability:{code}", "fund_availability", {"available": True})
    put_cache(disk_key, "fund_overview", result)
    return result
