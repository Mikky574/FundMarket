"""Collect an immutable industry snapshot and apply the frozen v1 scoring rules."""
from __future__ import annotations

import argparse
import json
import math
import time
from datetime import date, datetime
from pathlib import Path

import akshare as ak
import pandas as pd
import requests


CORE_NAMES = {
    "银行", "保险", "证券", "白酒", "半导体", "软件开发", "通信设备", "消费电子",
    "汽车整车", "汽车零部件", "光伏设备", "风电设备", "电池", "电网设备", "煤炭行业",
    "石油行业", "有色金属", "贵金属", "化学制药", "中药", "医疗服务", "医疗器械",
    "食品饮料", "家电行业", "房地产开发", "工程机械", "国防军工", "航天航空",
    "互联网服务", "文化传媒", "商业百货", "旅游酒店", "农牧饲渔", "小金属",
}


def safe_float(value, default=0.0):
    try:
        result = float(value)
        return default if math.isnan(result) else result
    except (TypeError, ValueError):
        return default


def pct(series: pd.Series, periods: int) -> float:
    return float((series.iloc[-1] / series.iloc[-periods - 1] - 1) * 100) if len(series) > periods else 0.0


def max_drawdown(series: pd.Series) -> float:
    peak = series.cummax()
    return float(((series / peak - 1) * 100).min()) if not series.empty else 0.0


def retry(call, attempts: int = 3):
    """Bounded retries without parallel bursts against a public data source."""
    error = None
    for attempt in range(attempts):
        try:
            return call()
        except Exception as exc:
            error = exc
            if attempt + 1 < attempts:
                time.sleep(1 + attempt * 2)
    raise error


def _industry_summary() -> tuple[pd.DataFrame, dict]:
    """Use Tonghuashun first; Eastmoney remains a compatibility fallback."""
    try:
        summary = retry(ak.stock_board_industry_summary_ths)
        names = retry(ak.stock_board_industry_name_ths)
        code_by_name = dict(zip(names["name"].astype(str), names["code"].astype(str)))
        frame = pd.DataFrame({
            "板块名称": summary["板块"].astype(str),
            "板块代码": summary["板块"].astype(str).map(code_by_name).fillna(""),
            "涨跌幅": pd.to_numeric(summary["涨跌幅"], errors="coerce"),
            "上涨家数": pd.to_numeric(summary["上涨家数"], errors="coerce").fillna(0),
            "下跌家数": pd.to_numeric(summary["下跌家数"], errors="coerce").fillna(0),
            "领涨股票": summary["领涨股"].astype(str),
            "换手率": None,
        })
        return frame, {"industry_spot": "tonghuashun"}
    except Exception as ths_error:
        frame = retry(ak.stock_board_industry_name_em)
        frame["板块名称"] = frame["板块名称"].astype(str)
        return frame, {"industry_spot": "eastmoney_fallback", "industry_spot_fallback_reason": str(ths_error)[:160]}


def history(name: str, start: str, end: str) -> tuple[dict | None, str]:
    """Tonghuashun industry daily series, with Eastmoney as per-industry fallback."""
    source = "tonghuashun"
    try:
        frame = ak.stock_board_industry_index_ths(symbol=name, start_date=start, end_date=end)
        close_column, volume_column, date_column = "收盘价", "成交量", "日期"
    except Exception:
        source = "eastmoney_fallback"
        try:
            frame = ak.stock_board_industry_hist_em(name, start_date=start, end_date=end, period="日k", adjust="")
            close_column, volume_column, date_column = "收盘", "成交量", "日期"
        except Exception:
            return None, source
    try:
        if len(frame) < 62:
            return None, source
        close = pd.to_numeric(frame[close_column], errors="coerce").dropna()
        volume = pd.to_numeric(frame[volume_column], errors="coerce").dropna()
        return {
            "date": pd.Timestamp(frame.iloc[-1][date_column]).date().isoformat(),
            "close": round(float(close.iloc[-1]), 4),
            "return_5d": round(pct(close, 5), 3),
            "return_20d": round(pct(close, 20), 3),
            "return_60d": round(pct(close, 60), 3),
            "ma20": round(float(close.tail(20).mean()), 4),
            "ma60": round(float(close.tail(60).mean()), 4),
            "volume_ratio_20d": round(float(volume.iloc[-1] / volume.tail(20).mean()), 3),
            "volatility_20d": round(float(close.pct_change().tail(20).std(ddof=0) * math.sqrt(252) * 100), 3),
            "drawdown_60d": round(max_drawdown(close.tail(60)), 3),
        }, source
    except Exception:
        return None, source


def _benchmark_tencent() -> tuple[dict, str]:
    """Tencent's public daily CSI 300 series avoids the Eastmoney push2 endpoint."""
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000300,day,,,320,qfq"
    response = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    rows = response.json()["data"]["sh000300"]["day"]
    close = pd.Series([float(row[2]) for row in rows])
    if len(close) < 21:
        raise ValueError("Tencent CSI 300 history is too short")
    return {
        "close": round(float(close.iloc[-1]), 3), "return_5d": round(pct(close, 5), 3),
        "return_20d": round(pct(close, 20), 3), "return_60d": round(pct(close, 60), 3),
        "ma20": round(float(close.tail(20).mean()), 3), "ma60": round(float(close.tail(60).mean()), 3),
        "volatility_20d": round(float(close.pct_change().tail(20).std(ddof=0) * math.sqrt(252) * 100), 3),
        "drawdown_60d": round(max_drawdown(close.tail(60)), 3),
    }, "tencent"


def _benchmark() -> tuple[dict, dict]:
    try:
        values, source = _benchmark_tencent()
        return values, {"benchmark": source}
    except Exception as tencent_error:
        frame = retry(lambda: ak.index_zh_a_hist("000300", start_date=(date.today() - pd.Timedelta(days=180)).strftime("%Y%m%d"), end_date=date.today().strftime("%Y%m%d")))
        close = pd.to_numeric(frame["收盘"], errors="coerce").dropna()
        return {"close": round(float(close.iloc[-1]), 3), "return_5d": round(pct(close, 5), 3),
                "return_20d": round(pct(close, 20), 3), "return_60d": round(pct(close, 60), 3),
                "ma20": round(float(close.tail(20).mean()), 3), "ma60": round(float(close.tail(60).mean()), 3),
                "volatility_20d": round(float(close.pct_change().tail(20).std(ddof=0) * math.sqrt(252) * 100), 3),
                "drawdown_60d": round(max_drawdown(close.tail(60)), 3)}, {"benchmark": "eastmoney_fallback", "benchmark_fallback_reason": str(tencent_error)[:160]}


def _percentile_ranks(values: list[float], higher_is_better: bool = True) -> list[float]:
    """Stable cross-sectional ranks; no factor is treated as an absolute prediction."""
    if not values:
        return []
    order = sorted(range(len(values)), key=lambda index: values[index], reverse=higher_is_better)
    result = [50.0] * len(values)
    denominator = max(1, len(values) - 1)
    for rank, index in enumerate(order):
        result[index] = round(100 * (1 - rank / denominator), 2)
    return result


def enrich_quantitative_structure(rows: list[dict], benchmark: dict) -> dict:
    """Replace binary thresholds and placeholder fundamentals with observable factors."""
    if not rows:
        return {"state": "insufficient_data", "data_coverage_percent": 0.0}
    trend_strength = [((row["close"] / row["ma20"] - 1) + (row["ma20"] / row["ma60"] - 1)) * 100 for row in rows]
    relative_20d = [row["return_20d"] - benchmark["return_20d"] for row in rows]
    participation = [row["up_count"] / max(1, row["up_count"] + row["down_count"]) * 100 for row in rows]
    volume = [row["volume_ratio_20d"] for row in rows]
    volatility = [row["volatility_20d"] for row in rows]
    drawdown = [abs(row["drawdown_60d"]) for row in rows]
    crowding = [abs(row["return_20d"]) for row in rows]
    ranks = {
        "trend": _percentile_ranks(trend_strength), "relative": _percentile_ranks(relative_20d),
        "participation": _percentile_ranks(participation), "volume": _percentile_ranks(volume),
        "volatility": _percentile_ranks(volatility, higher_is_better=False),
        "drawdown": _percentile_ranks(drawdown, higher_is_better=False),
        "crowding": _percentile_ranks(crowding, higher_is_better=False),
    }
    for index, row in enumerate(rows):
        trend = 0.65 * ranks["trend"][index] + 0.35 * _percentile_ranks([item["return_5d"] for item in rows])[index]
        relative = ranks["relative"][index]
        participation_score = 0.7 * ranks["participation"][index] + 0.3 * ranks["volume"][index]
        risk_control = 0.55 * ranks["volatility"][index] + 0.45 * ranks["drawdown"][index]
        total = 0.30 * trend + 0.25 * relative + 0.20 * participation_score + 0.15 * risk_control + 0.10 * ranks["crowding"][index]
        state = "uptrend" if row["close"] > row["ma20"] > row["ma60"] else "downtrend" if row["close"] < row["ma20"] < row["ma60"] else "mixed"
        row["scores"] = {
            "trend": round(trend, 2), "relative_strength": round(relative, 2),
            "participation": round(participation_score, 2), "risk_control": round(risk_control, 2),
            "crowding_control": round(ranks["crowding"][index], 2), "total": round(total, 2),
        }
        row["signals"] = {
            "trend_state": state, "relative_return_20d": round(relative_20d[index], 3),
            "breadth_percent": round(participation[index], 2), "volatility_20d": row["volatility_20d"],
            "drawdown_60d": row["drawdown_60d"],
        }
        row["data_confidence"] = 80 if row.get("history_source") == "tonghuashun" else 65
    above_ma20 = sum(row["close"] > row["ma20"] for row in rows) / len(rows) * 100
    average_relative = sum(relative_20d) / len(relative_20d)
    benchmark_up = benchmark["close"] > benchmark["ma20"] > benchmark["ma60"]
    state = "risk_on" if benchmark_up and above_ma20 >= 60 and average_relative >= 0 else "risk_off" if not benchmark_up and above_ma20 <= 40 else "mixed"
    return {"state": state, "benchmark_trend": "uptrend" if benchmark_up else "not_uptrend",
            "industry_above_ma20_percent": round(above_ma20, 2),
            "average_industry_relative_return_20d": round(average_relative, 3),
            "data_coverage_percent": round(len(rows) / max(1, len(rows)) * 100, 2),
            "interpretation": "市场状态由基准趋势、行业趋势扩散和行业相对强弱共同确定；不是价格预测。"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-industries", type=int, default=30)
    args = parser.parse_args()
    if not 10 <= args.max_industries <= 40:
        raise ValueError("max-industries must be between 10 and 40")
    today = date.today()
    start = (today - pd.Timedelta(days=180)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    spot, source_info = _industry_summary()
    leaders = spot.nlargest(10, "涨跌幅")["板块名称"].astype(str).tolist()
    laggards = spot.nsmallest(10, "涨跌幅")["板块名称"].astype(str).tolist()
    selected_names = list(dict.fromkeys(leaders + laggards + sorted(CORE_NAMES)))[:args.max_industries]
    selected = spot.set_index("板块名称").reindex(selected_names).dropna(how="all").reset_index()
    histories = {}
    for name in selected["板块名称"]:
        result, source = history(name, start, end)
        if result:
            result["history_source"] = source
            histories[name] = result
        # The SLA is ten minutes, so make public-source traffic steady instead of bursty.
        time.sleep(0.35)
    benchmark, benchmark_source = _benchmark()
    rows = []
    for _, item in selected.iterrows():
        name = item["板块名称"]
        if name not in histories:
            continue
        raw = {
            "name": name, "code": str(item["板块代码"]), "daily_return": safe_float(item["涨跌幅"]),
            "turnover_rate": safe_float(item["换手率"]), "up_count": int(safe_float(item["上涨家数"])),
            "down_count": int(safe_float(item["下跌家数"])), "leader": str(item["领涨股票"]),
            **histories[name],
        }
        rows.append(raw)
    market_regime = enrich_quantitative_structure(rows, benchmark)
    market_regime["data_coverage_percent"] = round(len(rows) / max(1, len(selected_names)) * 100, 2)
    rows.sort(key=lambda x: x["scores"]["total"], reverse=True)
    payload = {
        "snapshot_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_through": max((x["date"] for x in rows), default=None),
        "benchmark": {"name": "沪深300", **benchmark}, "sources": {**source_info, **benchmark_source},
        "method": "paper_fund_system_v1", "candidate_count": len(rows),
        "requested_industry_count": len(selected_names),
        "unavailable_industry_count": len(selected_names) - len(rows),
        "market_regime": market_regime,
        "collection_mode": "sequential_bounded_10_minute_batch", "industries": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "top10": [(x["name"], x["scores"]["total"]) for x in rows[:10]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
