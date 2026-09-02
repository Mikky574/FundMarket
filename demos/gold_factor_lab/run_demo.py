"""Run the standalone gold-factor demo without writing files or touching production state."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from demos.gold_factor_lab.analysis import describe
from demos.gold_factor_lab.collector import collect_factor_panel


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date.today() - timedelta(days=31))
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    panel = collect_factor_panel(start=args.start, end=args.end)
    print(json.dumps(describe(panel), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
