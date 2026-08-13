"""Collect reproducible historical fund NAV data for point-in-time evaluation.

Each published fund NAV is conservatively made visible at 20:00 Asia/Shanghai
on its NAV date.  This is intentionally later than market close, avoiding an
assumption that a same-day NAV was known during the trading session.
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fund", action="append", required=True, help="fund code; repeat for each fund")
    parser.add_argument("--start", required=True, help="inclusive YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="inclusive YYYY-MM-DD")
    parser.add_argument("--source", default="akshare:fund_open_fund_info_em")
    args = parser.parse_args()
    start, end = datetime.fromisoformat(args.start).date(), datetime.fromisoformat(args.end).date()
    if start > end:
        raise SystemExit("start must not be after end")
    timezone = ZoneInfo("Asia/Shanghai")
    rows = []
    for code in args.fund:
        frame = ak.fund_open_fund_info_em(code, "单位净值走势")
        for _, item in frame.iterrows():
            nav_date = datetime.strptime(str(item["净值日期"]), "%Y-%m-%d").date()
            if not start <= nav_date <= end:
                continue
            visible = datetime.combine(nav_date, datetime.min.time(), timezone).replace(hour=20)
            rows.append({"instrument": code, "as_of": nav_date.isoformat(), "close": str(item["单位净值"]),
                         "available_at": visible.isoformat(), "source": args.source})
    print(import_market(rows))


if __name__ == "__main__":
    main()
