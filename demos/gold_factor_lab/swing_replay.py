"""Fee-aware 5--10 session swing experiment for accumulated gold.

This is separate from the next-day classifier experiment. It uses a trend
regime as the primary signal; the local DeepSeek tool can only veto an entry
when it emits a high-confidence downside warning.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from demos.gold_factor_lab.blind_replay import Decision, _daily_rows, _macro_context, _sma, local_tool_decision
from demos.gold_factor_lab.collector import collect_factor_panel


SELL_FEE = 0.004
INITIAL_CASH = 100_000.0


def _trend_entry(history: list[dict]) -> tuple[bool, int]:
    if len(history) < 21:
        return False, 0
    prices = [float(row["price"]) for row in history]
    price, sma5, sma20 = prices[-1], _sma(prices, 5), _sma(prices, 20)
    momentum20 = price / prices[-21] - 1
    macro_score, macro_available, oil_risk, _labels = _macro_context(history)
    macro_ok = macro_available < 2 or macro_score >= 0
    return price > sma5 > sma20 and momentum20 >= 0.03 and macro_ok and not oil_risk, macro_score


def _model_veto(history: list[dict], *, in_position: bool, tool_url: str) -> Decision:
    neutral_rule = Decision("HOLD", 0.0, "trend regime handles entry", "swing_rule", "FLAT", 0.0)
    return local_tool_decision(history, in_position=in_position, rule=neutral_rule, tool_url=tool_url)


def replay(rows: list[dict], *, start: date, end: date, tool_url: str, use_deepseek: bool = True) -> dict:
    cash, grams, held_days, added = INITIAL_CASH, 0.0, 0, False
    trades, events, model_calls = [], [], 0
    for index in range(20, len(rows) - 1):
        row, fill = rows[index], rows[index + 1]
        signal_date = date.fromisoformat(row["observed_on"])
        if not start <= signal_date <= end:
            continue
        entry_ok, macro_score = _trend_entry(rows[:index + 1])
        exit_now = grams > 0 and held_days >= 5 and (not entry_ok or macro_score <= -2 or held_days >= 10)
        model = Decision("HOLD", 0, "not called", "not_called")
        veto = False
        if entry_ok and cash > 0 and not exit_now and (grams == 0 or (held_days >= 3 and not added)) and use_deepseek:
            model = _model_veto(rows[:index + 1], in_position=grams > 0, tool_url=tool_url)
            model_calls += 1
            veto = model.action == "SELL" and model.next_day_direction == "DOWN" and model.confidence >= 0.8 and model.direction_confidence >= 0.8
        action = "HOLD"
        if exit_now:
            action = "SELL"
        elif entry_ok and not veto and grams == 0:
            action = "BUY_50"
        elif entry_ok and not veto and grams > 0 and held_days >= 3 and not added:
            action = "ADD_25"
        events.append({"signal_day": row["observed_on"], "fill_day": fill["observed_on"], "entry_ok": entry_ok,
                       "macro_score": macro_score, "held_days": held_days, "model_action": model.action,
                       "model_direction": model.next_day_direction, "model_veto": veto, "executed": action})
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
    final_value = cash + grams * float(rows[-1]["price"])
    first_index = next(i for i, row in enumerate(rows) if date.fromisoformat(row["observed_on"]) >= start)
    buy_hold = INITIAL_CASH / float(rows[first_index]["price"]) * float(rows[-1]["price"]) * (1 - SELL_FEE)
    return {"strategy": "swing_v1_trend_5_to_10_sessions", "target_period": {"start": start.isoformat(), "end": end.isoformat()},
            "rule": {"entry": "price>SMA5>SMA20 and 20-session momentum>=3%; macro score>=0 when available", "sizing": "50% initial, one 25% add after 3 sessions", "exit": "after 5 sessions on trend failure/macro<=-2, mandatory exit by 10 sessions", "model": "DeepSeek may veto only a >=0.80 confidence DOWN/SELL warning", "fee": "buy 0%; sell 0.4%"},
            "initial_cash": INITIAL_CASH, "final_value": round(final_value, 2), "return_percent": round((final_value / INITIAL_CASH - 1) * 100, 3),
            "buy_and_hold_return_percent": round((buy_hold / INITIAL_CASH - 1) * 100, 3), "trade_count": len(trades), "fees_paid": round(sum(t["fee"] for t in trades), 2), "model_calls": model_calls, "trades": trades, "events": events,
            "limitations": ["This is a separate development strategy, not a replacement for the frozen next-day experiment.", "Only a holdout period not used to shape this rule may support an out-of-sample claim."]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-deepseek", action="store_true")
    parser.add_argument("--tool-url", default="http://127.0.0.1:8000/api/v1/internal/research/gold-blind-decision")
    args = parser.parse_args()
    panel = collect_factor_panel(start=args.start - timedelta(days=80), end=args.end)
    result = replay(_daily_rows(panel), start=args.start, end=args.end, tool_url=args.tool_url, use_deepseek=not args.no_deepseek)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
