"""Idempotent end-of-day runner for the one-month paper experiment."""
import argparse
import json
from pathlib import Path

from src.historical_evaluation.benchmark import BenchmarkTracker
from src.paths import PUBLIC_LEDGER_BENCHMARK_PATH, PUBLIC_LEDGER_REPORTS_ROOT, PUBLIC_LEDGER_STATE_PATH
from src.qq_control.paper_ledger import PaperLedger


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--portfolio", type=Path, default=PUBLIC_LEDGER_STATE_PATH)
    parser.add_argument("--benchmarks", type=Path, default=PUBLIC_LEDGER_BENCHMARK_PATH)
    parser.add_argument("--reports", type=Path, default=PUBLIC_LEDGER_REPORTS_ROOT)
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
