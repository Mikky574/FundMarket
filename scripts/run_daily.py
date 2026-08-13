"""Idempotent end-of-day runner for the one-month paper experiment."""
import argparse
import json
from pathlib import Path

from app.benchmark_engine import BenchmarkTracker
from app.paper_engine import PaperLedger


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--portfolio", type=Path, default=Path("paper/state.json"))
    parser.add_argument("--benchmarks", type=Path, default=Path("paper/benchmarks.json"))
    parser.add_argument("--reports", type=Path, default=Path("paper/reports"))
    args = parser.parse_args()
    ledger = PaperLedger(args.portfolio)
    result = ledger.daily_close(args.date)
    tracker = BenchmarkTracker(args.benchmarks)
    result["benchmark"] = tracker.update(args.date, args.portfolio)
    report = ledger.write_daily_report(result, args.reports)
    verification = ledger.verify_audit()
    if not verification["valid"]:
        raise RuntimeError(f"审计验证失败: {verification['reason']}")
    print(json.dumps({"date": args.date, "result": result, "report": str(report),
                      "audit": verification}, ensure_ascii=False))


if __name__ == "__main__":
    main()
