"""Print live JD ZheShang price snapshots without storing any market data."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from demos.gold_factor_lab.collector import collect_jd_intraday


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-seconds", type=float, default=15.0)
    parser.add_argument("--samples", type=int, default=1)
    args = parser.parse_args()
    if args.interval_seconds < 5:
        raise SystemExit("interval must be at least 5 seconds to respect the public source")
    if args.samples < 1:
        raise SystemExit("samples must be positive")
    for index in range(args.samples):
        rows = collect_jd_intraday()
        latest = rows[-1]
        source_at = datetime.fromisoformat(latest["source_at"])
        received_at = datetime.fromisoformat(latest["retrieved_at"])
        print(json.dumps({
            "snapshot": index + 1,
            "latest": latest,
            "intraday_points_returned": len(rows),
            "source_latency_seconds": round((received_at - source_at).total_seconds(), 3),
            "persistence": "disabled_in_demo",
        }, ensure_ascii=False), flush=True)
        if index + 1 < args.samples:
            time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
