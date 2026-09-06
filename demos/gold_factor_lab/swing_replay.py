"""Fee-aware 5--10 session swing experiment for accumulated gold.

This is separate from the next-day classifier experiment. It uses a trend
regime as the primary signal and has no model-driven decision path.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from demos.gold_factor_lab.blind_replay import _daily_rows, _macro_context, _sma
from demos.gold_factor_lab.collector import collect_factor_panel


SELL_FEE = 0.004
INITIAL_CASH = 100_000.0


def _trend_entry(history: list[dict]) -> tuple[bool, int]:
    if len(history) < 21:
        return False, 0
    prices = [float(row["price"]) for row in history]
    price, sma5, sma20 = prices[-1], _sma(prices, 5), _sma(prices, 20)
    momentum20 = price / prices[-21] - 1
    prior_resistance = max(prices[-21:-1])
    macro_score, macro_available, oil_risk, _labels = _macro_context(history)
    macro_ok = macro_available < 2 or macro_score >= 0
    continuation = price > sma5 > sma20 and momentum20 >= 0.03
    confirmed_breakout = price > sma20 and price >= prior_resistance * 1.003
    return (continuation or confirmed_breakout) and macro_ok and not oil_risk, macro_score


def replay(rows: list[dict], *, start: date, end: date) -> dict:
    """Run the deterministic legacy replay."""
    cash, grams, held_days, added = INITIAL_CASH, 0.0, 0, False
    trades, events = [], []
    for index in range(20, len(rows) - 1):
        row, fill = rows[index], rows[index + 1]
        signal_date = date.fromisoformat(row["observed_on"])
        if not start <= signal_date <= end:
            continue
        entry_ok, macro_score = _trend_entry(rows[:index + 1])
        prices = [float(item["price"]) for item in rows[:index + 1]]
        exit_now = grams > 0 and held_days >= 5 and (prices[-1] < _sma(prices, 10) or macro_score <= -2 or held_days >= 15)
        action = "HOLD"
        if exit_now:
            action = "SELL"
        elif entry_ok and grams == 0:
            action = "BUY_50"
        elif entry_ok and grams > 0 and held_days >= 3 and not added:
            action = "ADD_25"
        events.append({"signal_day": row["observed_on"], "fill_day": fill["observed_on"], "entry_ok": entry_ok,
                       "macro_score": macro_score, "held_days": held_days, "executed": action})
        price = float(fill["price"])
        if action in {"BUY_50", "ADD_25"}:
            amount = cash * 0.5 if action == "BUY_50" else min(cash, INITIAL_CASH * 0.25)
            grams += amount / price
            cash -= amount
            trades.append({"action": action, "signal_day": row["observed_on"], "fill_day": fill["observed_on"], "price": price, "notional": amount, "fee": 0.0})
            if action == "BUY_50":
                held_days, added = 0, False
            else:
                added = True
        elif action == "SELL":
            notional = grams * price
            fee = notional * SELL_FEE
            cash, grams = cash + notional - fee, 0.0
            trades.append({"action": "SELL", "signal_day": row["observed_on"], "fill_day": fill["observed_on"], "price": price, "notional": notional, "fee": fee})
            held_days, added = 0, False
        elif grams > 0:
            held_days += 1
    # The end of a research period is not a sell instruction.  Carry any open
    # gold at the observed quote and charge fees only on actual sell trades.
    mark_to_market_value = cash + grams * float(rows[-1]["price"])
    terminal_exit_fee = 0.0
    final_value = mark_to_market_value
    first_index = next(i for i, row in enumerate(rows) if date.fromisoformat(row["observed_on"]) >= start)
    buy_hold = INITIAL_CASH / float(rows[first_index]["price"]) * float(rows[-1]["price"])
    return {"strategy": "swing_v1_trend_5_to_10_sessions", "target_period": {"start": start.isoformat(), "end": end.isoformat()},
            "rule": {"entry": "trend continuation or >0.3% close above prior 20-session resistance; macro score>=0 when available", "sizing": "50% initial, one 25% add after 3 sessions", "exit": "after 5 sessions on close below SMA10/macro<=-2, mandatory exit by 15 sessions", "model": "DeepSeek is explanation-only and excluded from all decision and sizing paths", "fee": "buy 0%; sell 0.4%"},
            "initial_cash": INITIAL_CASH, "final_value": round(final_value, 2), "mark_to_market_value": round(mark_to_market_value, 2),
            "terminal_exit_fee": round(terminal_exit_fee, 2), "open_grams": round(grams, 8), "return_percent": round((final_value / INITIAL_CASH - 1) * 100, 3),
            "buy_and_hold_return_percent": round((buy_hold / INITIAL_CASH - 1) * 100, 3), "trade_count": len(trades), "realized_fees_paid": round(sum(t["fee"] for t in trades), 2),
            "fees_paid": round(sum(t["fee"] for t in trades), 2), "trades": trades, "events": events,
            "limitations": ["This is a separate development strategy, not a replacement for the frozen next-day experiment.", "Only a holdout period not used to shape this rule may support an out-of-sample claim."]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    panel = collect_factor_panel(start=args.start - timedelta(days=80), end=args.end)
    result = replay(_daily_rows(panel), start=args.start, end=args.end)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
