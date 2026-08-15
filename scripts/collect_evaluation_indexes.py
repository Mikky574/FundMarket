"""Collect benchmark and industry daily closes for isolated evaluation.

The script deliberately records an availability timestamp for every close.  A
daily close becomes visible at 18:00 Asia/Shanghai, so intraday historical AI
sessions cannot accidentally use the same day's closing level.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import akshare as ak

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.evaluation_service import import_market


DATE_COLUMNS = ("\u65e5\u671f", "date")
CLOSE_COLUMNS = ("\u6536\u76d8", "close")


def _column(frame, candidates: tuple[str, ...]) -> str:
    return next((column for column in candidates if column in frame.columns), "")


def _rows(instrument: str, frame, start, end, source: str) -> list[dict]:
    date_column, close_column = _column(frame, DATE_COLUMNS), _column(frame, CLOSE_COLUMNS)
    if not date_column or not close_column:
        raise RuntimeError(f"unexpected provider columns for {instrument}: {list(frame.columns)}")
    timezone = ZoneInfo("Asia/Shanghai")
    rows = []
    for _, item in frame.iterrows():
        as_of = datetime.fromisoformat(str(item[date_column])[:10]).date()
        if start <= as_of <= end:
            available = datetime.combine(as_of, datetime.min.time(), timezone).replace(hour=18)
            rows.append({"instrument": instrument, "as_of": as_of.isoformat(), "close": str(item[close_column]),
                         "available_at": available.isoformat(), "source": source})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="inclusive YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="inclusive YYYY-MM-DD")
    parser.add_argument("--benchmark", default="000300", help="A-share index code; default is CSI 300")
    parser.add_argument("--industry", action="append", default=[], help="Eastmoney industry name; repeatable")
    args = parser.parse_args()
    start, end = datetime.fromisoformat(args.start).date(), datetime.fromisoformat(args.end).date()
    if start > end:
        raise SystemExit("start must not be after end")
    compact_start, compact_end = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    benchmark = ak.index_zh_a_hist(symbol=args.benchmark, period="daily", start_date=compact_start, end_date=compact_end)
    rows = _rows(f"INDEX:{args.benchmark}", benchmark, start, end, "akshare:index_zh_a_hist")
    for name in args.industry:
        frame = ak.stock_board_industry_hist_em(symbol=name, start_date=compact_start, end_date=compact_end,
                                                period="\u65e5k", adjust="")
        rows.extend(_rows(f"INDUSTRY:{name}", frame, start, end, "akshare:stock_board_industry_hist_em"))
    print(import_market(rows))


if __name__ == "__main__":
    main()
