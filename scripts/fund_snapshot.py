"""Save fund NAV, disclosed holdings and holding-stock trend evidence."""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

import akshare as ak
import pandas as pd


def stock_trend(symbol: str, start: str, end: str) -> dict:
    frame = ak.stock_zh_a_hist(symbol=symbol, start_date=start, end_date=end, adjust="qfq")
    close = pd.to_numeric(frame["收盘"], errors="coerce").dropna()
    def ret(n): return round(float((close.iloc[-1] / close.iloc[-n - 1] - 1) * 100), 3) if len(close) > n else None
    return {"date": pd.Timestamp(frame.iloc[-1]["日期"]).date().isoformat(), "close": float(close.iloc[-1]),
            "return_5d": ret(5), "return_20d": ret(20), "above_ma20": bool(close.iloc[-1] > close.tail(20).mean())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--year", default=str(date.today().year))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    nav = ak.fund_open_fund_info_em(args.code, "单位净值走势")
    points = [{"date": pd.Timestamp(r["净值日期"]).date().isoformat(), "nav": float(r["单位净值"]),
               "daily_return": None if pd.isna(r.get("日增长率")) else float(r["日增长率"])} for _, r in nav.iterrows()]
    holdings = ak.fund_portfolio_hold_em(args.code, args.year)
    latest_quarter = sorted(holdings["季度"].unique())[-1]
    latest = holdings[holdings["季度"] == latest_quarter].head(10)
    holding_rows = [{"symbol": str(r["股票代码"]).zfill(6), "name": str(r["股票名称"]),
                     "weight": float(r["占净值比例"])} for _, r in latest.iterrows()]
    start = (date.today() - pd.Timedelta(days=90)).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(stock_trend, h["symbol"], start, end): h for h in holding_rows}
        for future in as_completed(futures):
            try: futures[future]["trend"] = future.result()
            except Exception as exc: futures[future]["trend_error"] = str(exc)
    recent = pd.Series([x["nav"] for x in points[-250:]])
    returns = recent.pct_change().dropna()
    peak = recent.cummax()
    result = {
        "snapshot_at": datetime.now().astimezone().isoformat(timespec="seconds"), "data_through": points[-1]["date"],
        "code": args.code, "name": args.name, "latest_nav": points[-1],
        "metrics": {
            "return_5d": round((points[-1]["nav"] / points[-6]["nav"] - 1) * 100, 3),
            "return_20d": round((points[-1]["nav"] / points[-21]["nav"] - 1) * 100, 3),
            "volatility_1y_annualized": round(float(returns.std() * (250 ** 0.5) * 100), 3),
            "max_drawdown_1y": round(float(((recent / peak) - 1).min() * 100), 3),
        },
        "holding_report": latest_quarter, "holdings": holding_rows,
        "nav_history": points[-250:],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "latest": result["latest_nav"], "metrics": result["metrics"],
                      "holdings": [(x["name"], x["weight"], x.get("trend", {}).get("return_20d")) for x in holding_rows]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
