import argparse
import json
from pathlib import Path

from app.benchmark_engine import BenchmarkTracker


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=Path("paper/benchmarks.json"))
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init"); init.add_argument("--date", required=True); init.add_argument("--fund-code", required=True)
    init.add_argument("--fund-name", required=True); init.add_argument("--industry", required=True)
    update = sub.add_parser("update"); update.add_argument("--date", required=True)
    update.add_argument("--portfolio-state", type=Path, default=Path("paper/state.json"))
    sub.add_parser("status")
    args = parser.parse_args(); tracker = BenchmarkTracker(args.state)
    if args.command == "init": tracker.initialize(args.date, args.fund_code, args.fund_name, args.industry)
    elif args.command == "update": print(json.dumps(tracker.update(args.date, args.portfolio_state), ensure_ascii=False))
    print(json.dumps(tracker.state, ensure_ascii=False))


if __name__ == "__main__": main()

