"""Point-in-time daily replay for the isolated gold-factor experiment.

This is a research backtest, never an order path.  A signal at day *t* uses
only rows through day *t* and is filled at the next available daily quote.  The
optional DeepSeek adapter receives sequential day numbers, not calendar dates.
"""
from __future__ import annotations

import argparse
import json
import sys
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from demos.gold_factor_lab.collector import collect_factor_panel


FEE_RATE = 0.004  # 0.4% charged only when selling.
INITIAL_CASH = 100_000.0
WARMUP_DAYS = 20


@dataclass(frozen=True)
class Decision:
    action: str
    confidence: float
    reason: str
    source: str
    next_day_direction: str = "FLAT"
    direction_confidence: float = 0.0
    macro_score: int = 0
    macro_available: int = 0
    horizon_direction: str = "FLAT"
    horizon_confidence: float = 0.0
    technical_regime: str = "unknown"


def third_prior_month(reference: date) -> tuple[date, date]:
    """Return the complete calendar month three months before ``reference``."""
    month_number = reference.year * 12 + reference.month - 1 - 3
    year, month_index = divmod(month_number, 12)
    month = month_index + 1
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def _sma(values: list[float], period: int) -> float | None:
    return sum(values[-period:]) / period if len(values) >= period else None


def _macro_context(history: list[dict]) -> tuple[int, int, bool, list[str]]:
    """Score established gold drivers from prior-session values only."""
    if len(history) < 6:
        return 0, 0, False, []
    current, prior = history[-1], history[-6]
    score, available, labels = 0, 0, []
    for name, unit, support_threshold, pressure_threshold, support_is_higher in (
        ("usd_cny", "return", 0.001, -0.001, True),
        ("broad_us_dollar", "return", -0.001, 0.001, False),
        ("us_10y_real_yield", "level", -0.03, 0.03, False),
    ):
        if current.get(name) is None or prior.get(name) is None:
            continue
        available += 1
        change = float(current[name]) - float(prior[name]) if unit == "level" else float(current[name]) / float(prior[name]) - 1
        supports = change >= support_threshold if support_is_higher else change <= support_threshold
        pressures = change <= pressure_threshold if support_is_higher else change >= pressure_threshold
        if supports:
            score += 1
            labels.append(f"{name}:support")
        elif pressures:
            score -= 1
            labels.append(f"{name}:pressure")
    oil_risk = current.get("wti_crude") is not None and prior.get("wti_crude") is not None and abs(float(current["wti_crude"]) / float(prior["wti_crude"]) - 1) >= 0.05
    return score, available, oil_risk, labels


def _technical_context(history: list[dict]) -> dict[str, float | None]:
    """Compute date-free support, resistance and trend features for the LLM."""
    prices = [float(row["price"]) for row in history]
    if len(prices) < 21:
        return {"sma5": None, "sma20": None, "momentum5_pct": None, "momentum20_pct": None,
                "distance_to_resistance20_pct": None, "distance_to_support20_pct": None}
    resistance, support = max(prices[-21:-1]), min(prices[-21:-1])
    return {"sma5": round(_sma(prices, 5), 4), "sma20": round(_sma(prices, 20), 4),
            "momentum5_pct": round((prices[-1] / prices[-6] - 1) * 100, 3),
            "momentum20_pct": round((prices[-1] / prices[-21] - 1) * 100, 3),
            "distance_to_resistance20_pct": round((prices[-1] / resistance - 1) * 100, 3),
            "distance_to_support20_pct": round((prices[-1] / support - 1) * 100, 3)}


def rule_decision(history: list[dict], *, in_position: bool, held_days: int, cooldown_days: int) -> Decision:
    """A conservative, fee-aware trend rule with hysteresis.

    Entry requires a confirmed short/medium trend.  Exit requires a meaningful
    trend failure, so a 0.8% round trip fee is not repeatedly paid for noise.
    """
    prices = [float(row["price"]) for row in history]
    if len(prices) < 12:
        return Decision("HOLD", 0.0, "warm-up data is incomplete", "rule", "FLAT", 0.0)
    price, sma5, sma12 = prices[-1], _sma(prices, 5), _sma(prices, 12)
    momentum5 = price / prices[-6] - 1 if len(prices) >= 6 else 0.0
    drawdown10 = price / max(prices[-10:]) - 1
    macro_score, macro_available, oil_risk, labels = _macro_context(history)
    macro_allows_entry = macro_available < 2 or macro_score >= 1
    trend_up = price > sma5 > sma12 and momentum5 >= 0.012 and drawdown10 >= -0.007 and macro_allows_entry and not oil_risk
    trend_failed = (price < sma5 and momentum5 <= -0.008) or drawdown10 <= -0.016 or (macro_available >= 2 and macro_score <= -2)
    if not in_position and cooldown_days == 0 and trend_up:
        return Decision("BUY", 0.7, "price trend and macro drivers confirm entry: " + ", ".join(labels), "rule", "UP", 0.7, macro_score, macro_available)
    if in_position and held_days >= 3 and trend_failed:
        return Decision("SELL", 0.75, "trend or macro context failed", "rule", "DOWN", 0.75, macro_score, macro_available)
    return Decision("HOLD", 0.4, "no fee-aware trend and macro transition", "rule", "FLAT", 0.4, macro_score, macro_available)


def anonymised_prompt(history: list[dict], *, in_position: bool, rule: Decision) -> str:
    """Create the exact model input; it intentionally has no calendar date."""
    rows = []
    start_index = max(0, len(history) - WARMUP_DAYS)
    for absolute_index, row in enumerate(history[start_index:], start=start_index):
        rows.append({
            "day": absolute_index + 1,
            "gold_cny_per_gram": round(float(row["price"]), 4),
            "gold_return_1d_pct": round(float(row.get("return_1d", 0)) * 100, 3),
            "usd_cny": row.get("usd_cny"),
            "broad_us_dollar": row.get("broad_us_dollar"),
            "us_10y_real_yield": row.get("us_10y_real_yield"),
            "us_10y_nominal_yield": row.get("us_10y_nominal_yield"),
            "wti_crude": row.get("wti_crude"),
            **_technical_context(history[:absolute_index + 1]),
        })
    payload = {
        "experiment": "daily historical blind replay",
        "calendar_dates": "intentionally omitted",
        "execution": "a decision after this row fills at the next observed daily quote; sell fee is 0.4%, buy fee is zero",
        "position": "long_gold" if in_position else "cash",
        "rule_candidate": rule.action,
        "recent_observations": rows,
    }
    return (
        "You are a constrained research classifier, not an investment adviser. "
        "Do not infer dates and do not ask for future data. Return JSON only: "
        '{"next_day_direction":"UP|DOWN|FLAT","direction_confidence":0..1,"horizon_5_10_direction":"UP|DOWN|FLAT","horizon_confidence":0..1,"technical_regime":"breakout|trend_continuation|near_resistance|near_support|range|breakdown|uncertain","action":"BUY|SELL|HOLD","confidence":0..1,"reason":"under 160 chars"}. '
        "BUY means move from cash to gold; SELL means move from gold to cash. "
        "Use only the sequential observations supplied below.\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def local_tool_decision(history: list[dict], *, in_position: bool, rule: Decision, tool_url: str,
                        analysis_mode: str = "technical_breakout") -> Decision:
    """Use the project's local DeepSeek capability; no credential enters this script."""
    payload = json.loads(anonymised_prompt(history, in_position=in_position, rule=rule).split("\n", 1)[1])
    response = httpx.post(tool_url, json={"position": payload["position"], "rule_candidate": rule.action,
                                           "observations": payload["recent_observations"], "analysis_mode": analysis_mode}, timeout=60)
    response.raise_for_status()
    data = response.json()
    action = str(data.get("action", "HOLD")).upper()
    if action not in {"BUY", "SELL", "HOLD"}:
        action = "HOLD"
    confidence = min(1.0, max(0.0, float(data.get("confidence", 0))))
    direction = str(data.get("next_day_direction", "FLAT")).upper()
    if direction not in {"UP", "DOWN", "FLAT"}:
        direction = "FLAT"
    direction_confidence = min(1.0, max(0.0, float(data.get("direction_confidence", 0))))
    horizon_direction = str(data.get("horizon_5_10_direction", "FLAT")).upper()
    if horizon_direction not in {"UP", "DOWN", "FLAT"}:
        horizon_direction = "FLAT"
    horizon_confidence = min(1.0, max(0.0, float(data.get("horizon_confidence", 0))))
    regime = str(data.get("technical_regime", "unknown"))[:40]
    return Decision(action, confidence, str(data.get("reason", "no reason"))[:160], "deepseek_tool", direction, direction_confidence, 0, 0, horizon_direction, horizon_confidence, regime)


def _daily_rows(panel: dict[str, list[dict]]) -> list[dict]:
    """Join only information observed no later than each gold quote's day."""
    factors = {name: {row["observed_on"]: float(row["value"]) for row in rows}
               for name, rows in panel.items() if name != "jd_zheshang_gold"}
    latest: dict[str, float | None] = {name: None for name in factors}
    output = []
    for gold in sorted(panel["jd_zheshang_gold"], key=lambda row: row["observed_on"]):
        day = gold["observed_on"]
        # The free source has no verified US release timestamps. A gold row may
        # therefore use factor values only from strictly earlier calendar days.
        output.append({"observed_on": day, "price": float(gold["value"]), **latest})
        for name, values in factors.items():
            if day in values:
                latest[name] = values[day]
    for index, row in enumerate(output):
        row["return_1d"] = 0.0 if index == 0 else row["price"] / output[index - 1]["price"] - 1
    return output


def replay(rows: list[dict], *, trade_start: date, fee_rate: float = FEE_RATE,
           initial_cash: float = INITIAL_CASH, decision_provider: Callable[[list[dict], bool, Decision], Decision] | None = None) -> dict:
    """Run an all-cash/all-gold replay; decisions are made at close, filled next day."""
    if fee_rate < 0 or fee_rate >= 1:
        raise ValueError("fee_rate must be in [0, 1)")
    decision_provider = decision_provider or (lambda _history, _position, rule: rule)
    cash, grams, held_days, cooldown = initial_cash, 0.0, 0, 0
    trades, decisions = [], []
    for index in range(WARMUP_DAYS, len(rows) - 1):
        row, fill = rows[index], rows[index + 1]
        if date.fromisoformat(row["observed_on"]) < trade_start:
            continue
        rule = rule_decision(rows[:index + 1], in_position=grams > 0, held_days=held_days, cooldown_days=cooldown)
        model = decision_provider(rows[:index + 1], grams > 0, rule)
        # The model may veto a rule entry or request a sufficiently confident
        # protective exit. An unsupported model BUY is never allowed.
        action = "SELL" if rule.action == "SELL" or (grams > 0 and model.action == "SELL" and model.confidence >= 0.7 and model.next_day_direction == "DOWN" and model.direction_confidence >= 0.65) else ("BUY" if rule.action == "BUY" and model.action == "BUY" and model.confidence >= 0.7 and model.next_day_direction == "UP" and model.direction_confidence >= 0.65 else "HOLD")
        next_return = float(fill["price"]) / float(row["price"]) - 1
        actual_direction = "UP" if next_return > 0 else "DOWN" if next_return < 0 else "FLAT"
        decisions.append({"signal_day": row["observed_on"], "fill_day": fill["observed_on"], "rule": rule.action,
                          "model": model.action, "model_confidence": model.confidence,
                          "next_day_direction": model.next_day_direction, "direction_confidence": model.direction_confidence,
                          "macro_score": rule.macro_score, "macro_available": rule.macro_available,
                          "actual_next_day_direction": actual_direction, "actual_next_day_return_percent": round(next_return * 100, 4), "executed": action})
        if action == "BUY" and cash > 0:
            notional, fee = cash, 0.0
            grams, cash = notional / float(fill["price"]), 0.0
            trades.append({"action": "BUY", "signal_day": row["observed_on"], "fill_day": fill["observed_on"], "price": fill["price"], "notional": notional, "fee": fee})
            held_days, cooldown = 0, 0
        elif action == "SELL" and grams > 0:
            notional = grams * float(fill["price"])
            fee = notional * fee_rate
            cash, grams = notional - fee, 0.0
            trades.append({"action": "SELL", "signal_day": row["observed_on"], "fill_day": fill["observed_on"], "price": fill["price"], "notional": notional, "fee": fee})
            held_days, cooldown = 0, 2
        elif grams > 0:
            held_days += 1
        elif cooldown:
            cooldown -= 1
    final_value = cash + grams * float(rows[-1]["price"])
    benchmark_signal_index = next(index for index, row in enumerate(rows[:-1]) if index >= WARMUP_DAYS and date.fromisoformat(row["observed_on"]) >= trade_start)
    buy_hold_grams = initial_cash / float(rows[benchmark_signal_index + 1]["price"])
    buy_hold_value = buy_hold_grams * float(rows[-1]["price"]) * (1 - fee_rate)
    directional = [item for item in decisions if item["next_day_direction"] in {"UP", "DOWN"}]
    correct = [item for item in directional if item["next_day_direction"] == item["actual_next_day_direction"]]
    up_calls = [item for item in directional if item["next_day_direction"] == "UP"]
    up_correct = [item for item in up_calls if item["actual_next_day_direction"] == "UP"]
    return {"contract": {"signal": "daily close", "execution": "next observed daily quote", "buy_fee_rate": 0.0, "sell_fee_rate": fee_rate,
                           "availability": "exploratory assumed daily-close availability; not verified vendor release time"},
            "frozen_rule": {"entry": "price>SMA5>SMA12, 5-day momentum >=1.2%, 10-day drawdown >=-0.7%, plus macro score >=1 when >=2 factors are available", "macro": "USD/CNY higher, real yield lower, broad dollar lower each add +1; inverse moves subtract 1; >=5% five-day oil move blocks entry", "model_gate": "BUY/UP requires action confidence >=0.70 and direction confidence >=0.65", "exit": "trend failure, 10-day drawdown <=-1.6%, or macro score <=-2", "anti_churn": "minimum 3 holding days; 2-day post-sale cooldown", "selection": "set before the next untouched validation period; no target-month parameter fitting"},
            "initial_cash": initial_cash, "final_value": round(final_value, 2), "return_percent": round((final_value / initial_cash - 1) * 100, 3),
            "buy_and_hold_final_value": round(buy_hold_value, 2), "buy_and_hold_return_percent": round((buy_hold_value / initial_cash - 1) * 100, 3),
            "fees_paid": round(sum(trade["fee"] for trade in trades), 2), "trade_count": len(trades),
            "prediction_metrics": {"all_days": len(decisions), "directional_calls": len(directional), "directional_accuracy_percent": round(100 * len(correct) / len(directional), 2) if directional else None, "up_call_precision_percent": round(100 * len(up_correct) / len(up_calls), 2) if up_calls else None}, "trades": trades, "decisions": decisions,
            "limitations": ["The historical JD chart was retrieved after the fact; replay assumes each quoted daily point was usable after its own close.", "This is one historical month, not evidence that a rule is profitable out of sample.", "DeepSeek output is constrained by deterministic risk gates and is not a trading instruction."]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Blind, daily gold replay; writes an isolated research artifact.")
    parser.add_argument("--month", type=date.fromisoformat, help="Any date in the target month; default is three months prior.")
    parser.add_argument("--start", type=date.fromisoformat, help="Explicit inclusive target start; must be paired with --end.")
    parser.add_argument("--end", type=date.fromisoformat, help="Explicit inclusive target end; must be paired with --start.")
    parser.add_argument("--deepseek", action="store_true", help="Use the local project's DeepSeek research capability.")
    parser.add_argument("--tool-url", default="http://127.0.0.1:8000/api/v1/internal/research/gold-blind-decision")
    parser.add_argument("--output", type=Path, required=True, help="Ignored research-output path, e.g. data/gold_lab/evaluations/june.json")
    args = parser.parse_args()
    if bool(args.start) != bool(args.end):
        parser.error("--start and --end must be provided together")
    if args.start and args.month:
        parser.error("choose either --month or --start/--end")
    if args.start:
        start, end = args.start, args.end
        if start > end:
            parser.error("--start must not be after --end")
    elif args.month:
        start, end = date(args.month.year, args.month.month, 1), date(args.month.year, args.month.month, monthrange(args.month.year, args.month.month)[1])
    else:
        start, end = third_prior_month(date.today())
    panel = collect_factor_panel(start=start - timedelta(days=80), end=end)
    rows = _daily_rows(panel)
    provider = None
    if args.deepseek:
        provider = lambda history, position, rule: local_tool_decision(history, in_position=position, rule=rule, tool_url=args.tool_url)
    result = replay(rows, trade_start=start, decision_provider=provider)
    result["target_period"] = {"start": start.isoformat(), "end": end.isoformat()}
    result["strategy"] = "deepseek_guarded" if args.deepseek else "deterministic_rule_baseline"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
