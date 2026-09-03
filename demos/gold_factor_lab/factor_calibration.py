"""Development-only factor calibration for the gold replay rule.

It does not fit or alter DeepSeek.  It measures a small, predeclared set of
macro contexts on a designated development period, then records whether there
is enough evidence to keep each context as a gate before an untouched holdout.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from demos.gold_factor_lab.blind_replay import WARMUP_DAYS, _daily_rows, _macro_context
from demos.gold_factor_lab.collector import collect_factor_panel


def _stats(returns: list[float]) -> dict:
    return {
        "observations": len(returns),
        "up_rate_percent": round(100 * sum(value > 0 for value in returns) / len(returns), 2) if returns else None,
        "mean_next_day_return_percent": round(100 * sum(returns) / len(returns), 4) if returns else None,
    }


def calibrate(rows: list[dict], *, start: date, end: date) -> dict:
    """Measure fixed factor contexts; do not optimise thresholds on the holdout."""
    groups = {"all": [], "macro_support_score_ge_1": [], "macro_pressure_score_le_minus_1": [],
              "macro_neutral": [], "high_oil_move": []}
    for index in range(WARMUP_DAYS, len(rows) - 1):
        day = date.fromisoformat(rows[index]["observed_on"])
        if not start <= day <= end:
            continue
        future_return = float(rows[index + 1]["price"]) / float(rows[index]["price"]) - 1
        score, _available, oil_risk, _labels = _macro_context(rows[:index + 1])
        groups["all"].append(future_return)
        if score >= 1:
            groups["macro_support_score_ge_1"].append(future_return)
        elif score <= -1:
            groups["macro_pressure_score_le_minus_1"].append(future_return)
        else:
            groups["macro_neutral"].append(future_return)
        if oil_risk:
            groups["high_oil_move"].append(future_return)
    report = {name: _stats(values) for name, values in groups.items()}
    support = report["macro_support_score_ge_1"]
    baseline = report["all"]
    keep_support_gate = bool(support["observations"] >= 8 and support["up_rate_percent"] >= (baseline["up_rate_percent"] or 0) + 5)
    return {"development_period": {"start": start.isoformat(), "end": end.isoformat()}, "development_only": True,
            "predeclared_contexts": report,
            "decision": {"keep_macro_support_gate": keep_support_gate,
                         "rule": "keep only if >=8 observations and up-rate exceeds all-day rate by >=5 percentage points"},
            "limitations": ["This calibrates a deterministic gate, not DeepSeek model weights.", "The selected period must never be used to claim out-of-sample performance.", "Use an untouched later period for validation without changing the chosen rule."]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.start > args.end:
        parser.error("--start must not be after --end")
    panel = collect_factor_panel(start=args.start - timedelta(days=80), end=args.end)
    result = calibrate(_daily_rows(panel), start=args.start, end=args.end)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
