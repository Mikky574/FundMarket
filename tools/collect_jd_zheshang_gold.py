"""Print JD ZheShang gold observations as JSON; this tool never writes data."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.quant_research.gold_prices import fetch_latest, fetch_one_month_history


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history-availability", choices=("strict", "assumed_eod"), default="strict")
    parser.add_argument("--include-latest", action="store_true")
    args = parser.parse_args()
    result = {
        "history": fetch_one_month_history(historical_availability=args.history_availability),
        "history_availability_mode": args.history_availability,
    }
    if args.include_latest:
        result["latest"] = fetch_latest()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
