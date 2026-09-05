"""Fee-aware, point-in-time swing-v3 research replay for accumulated gold.

The deterministic layer finds trend breakouts and pullback resumptions, sizes
positions, and exits them.  The optional DeepSeek panel can only reduce risk;
it cannot manufacture an entry.  This module is intentionally separate from
``swing_replay.py`` so prior research artifacts remain reproducible.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from demos.gold_factor_lab.blind_replay import Decision, _daily_rows, _macro_context, local_tool_decision
from demos.gold_factor_lab.collector import collect_factor_panel
from src.quant_research.contracts import BlindGoldAnalysisContext


SELL_FEE = 0.004
INITIAL_CASH = 100_000.0


@dataclass(frozen=True)
class SwingV3Config:
    """Frozen, fee-aware strategy parameters for one replay version."""

    warmup_sessions: int = 65
    sell_fee: float = SELL_FEE
    core_weight: float = 0.25
    satellite_initial_weight: float = 0.35
    satellite_add_one_weight: float = 0.25
    satellite_add_two_weight: float = 0.15
    breakout_sigma: float = 0.25
    add_profit_sigma: float = 0.75
    initial_stop_sigma: float = 1.50
    trail_activation_sigma: float = 1.00
    trail_sigma: float = 2.00
    no_progress_sessions: int = 20
    volatility_floor: float = 0.001


@dataclass
class PositionState:
    cash: float
    core_grams: float = 0.0
    satellite_grams: float = 0.0
    satellite_average_entry: float = 0.0
    satellite_entry_volatility: float = 0.0
    max_close: float = 0.0
    held_sessions: int = 0
    add_stage: int = 0
    below_ema20_streak: int = 0
    below_sma60_streak: int = 0


PanelProvider = Callable[[list[dict], dict, PositionState], dict[str, Decision]]


def _sma(values: list[float], period: int) -> float | None:
    return sum(values[-period:]) / period if len(values) >= period else None


def _ema(values: list[float], period: int) -> float | None:
    """EMA seeded by the first complete SMA, avoiding any future values."""
    if len(values) < period:
        return None
    value = sum(values[:period]) / period
    multiplier = 2 / (period + 1)
    for price in values[period:]:
        value += multiplier * (price - value)
    return value


def _realized_volatility(prices: list[float], *, period: int, floor: float) -> float | None:
    if len(prices) < period + 1:
        return None
    returns = [prices[index] / prices[index - 1] - 1 for index in range(len(prices) - period, len(prices))]
    return max(statistics.stdev(returns), floor)


def features(history: list[dict], *, config: SwingV3Config = SwingV3Config()) -> dict:
    """Build only t-or-earlier features for one daily-close signal."""
    prices = [float(item["price"]) for item in history]
    if len(prices) < config.warmup_sessions:
        raise ValueError("swing-v3 requires its configured warm-up history")
    price = prices[-1]
    ema5, ema20, sma60 = _ema(prices, 5), _ema(prices, 20), _sma(prices, 60)
    prior_ema5, ema20_five_sessions_ago = _ema(prices[:-1], 5), _ema(prices[:-5], 20)
    sma60_five_sessions_ago = _sma(prices[:-5], 60)
    volatility = _realized_volatility(prices, period=20, floor=config.volatility_floor)
    assert ema5 is not None and ema20 is not None and sma60 is not None
    assert prior_ema5 is not None and ema20_five_sessions_ago is not None and sma60_five_sessions_ago is not None and volatility is not None
    resistance20, support20 = max(prices[-21:-1]), min(prices[-21:-1])
    macro_score, macro_available, oil_risk, macro_labels = _macro_context(history)
    return {
        "price": price,
        "ema5": ema5,
        "ema20": ema20,
        "sma60": sma60,
        "prior_ema5": prior_ema5,
        "ema20_five_sessions_ago": ema20_five_sessions_ago,
        "sma60_five_sessions_ago": sma60_five_sessions_ago,
        "volatility": volatility,
        "resistance20": resistance20,
        "support20": support20,
        "high10": max(prices[-11:-1]),
        "low5": min(prices[-6:-1]),
        "previous_price": prices[-2],
        "uptrend": price > ema20 > sma60 and ema20 > ema20_five_sessions_ago,
        "long_trend": price > sma60 and ema20 > sma60 and sma60 >= sma60_five_sessions_ago,
        "macro_score": macro_score,
        "macro_available": macro_available,
        "oil_risk": oil_risk,
        "macro_labels": macro_labels,
    }


def entry_kind(feature: dict, *, config: SwingV3Config = SwingV3Config()) -> str:
    """Return a deterministic entry setup, never a model-created signal."""
    if not feature["uptrend"]:
        return "none"
    price, volatility = feature["price"], feature["volatility"]
    breakout = price / feature["resistance20"] - 1 >= config.breakout_sigma * volatility
    retracement = feature["high10"] / feature["low5"] - 1
    reclaim = price > feature["ema5"] and feature["previous_price"] <= feature["prior_ema5"]
    pullback = config.breakout_sigma * 2 * volatility <= retracement <= 3 * volatility and reclaim
    if breakout:
        return "breakout"
    return "pullback" if pullback else "none"


def _macro_multiplier(feature: dict) -> float:
    """Macro context changes size, not whether a technical setup exists."""
    if feature["macro_available"] < 2:
        return 0.75
    if feature["macro_score"] >= 1 and not feature["oil_risk"]:
        return 1.0
    if feature["macro_score"] >= 0:
        return 0.75
    return 0.50


def _position_weight(state: PositionState, price: float) -> float:
    value = state.cash + _total_grams(state) * price
    return 0.0 if value <= 0 else _total_grams(state) * price / value


def _total_grams(state: PositionState) -> float:
    return state.core_grams + state.satellite_grams


def _standardised_observations(history: list[dict]) -> list[dict]:
    """Keep the model window date-free and free of absolute CNY price levels."""
    window = history[-20:]
    base_price = float(window[0]["price"])
    return [
        {
            "day": offset,
            "gold_index_base100": round(float(row["price"]) / base_price * 100, 4),
            "gold_return_1d_pct": round(float(row.get("return_1d", 0)) * 100, 4),
        }
        for offset, row in enumerate(window, start=1)
    ]


def analysis_context(history: list[dict], feature: dict, state: PositionState, *, stage: str,
                     candidate: str, config: SwingV3Config = SwingV3Config()) -> dict:
    """Create the strict, feature-only context sent to every panel role."""
    price = feature["price"]
    max_factor_age = 1 if feature["macro_available"] else 20
    return BlindGoldAnalysisContext(
        sequence=len(history),
        candidate_stage=stage,
        requested_horizon_sessions=[5, 10],
        current_weight_pct=round(_position_weight(state, price) * 100, 4),
        held_sessions=state.held_sessions,
        buy_fee_pct=0.0,
        sell_fee_pct=round(config.sell_fee * 100, 4),
        max_factor_age_sessions=max_factor_age,
        candidate_kind=candidate,
        realized_volatility_20d_pct=round(feature["volatility"] * 100, 4),
        price_vs_ema5_pct=round((price / feature["ema5"] - 1) * 100, 4),
        price_vs_ema20_pct=round((price / feature["ema20"] - 1) * 100, 4),
        price_vs_sma60_pct=round((price / feature["sma60"] - 1) * 100, 4),
        ema20_slope_5d_pct=round((feature["ema20"] / feature["ema20_five_sessions_ago"] - 1) * 100, 4),
        distance_to_resistance20_pct=round((price / feature["resistance20"] - 1) * 100, 4),
        distance_to_support20_pct=round((price / feature["support20"] - 1) * 100, 4),
        macro_score=feature["macro_score"],
        macro_available=feature["macro_available"],
    ).model_dump(exclude_none=True)


def _model_panel(history: list[dict], feature: dict, state: PositionState, *, stage: str,
                 candidate: str, tool_url: str, config: SwingV3Config) -> dict[str, Decision]:
    """Ask distinct research roles once per setup; none owns execution."""
    neutral_rule = Decision("HOLD", 0.0, "deterministic swing-v3 candidate", "swing_v3")
    context = analysis_context(history, feature, state, stage=stage, candidate=candidate, config=config)
    observations = _standardised_observations(history)
    return {
        mode: local_tool_decision(
            history,
            in_position=_total_grams(state) > 0,
            rule=neutral_rule,
            tool_url=tool_url,
            analysis_mode=mode,
            analysis_context=context,
            observations=observations,
        )
        for mode in ("technical_breakout", "macro_regime", "trade_quality", "risk_skeptic")
    }


def _fuse_panel(panel: dict[str, Decision]) -> dict:
    """Translate model risk into a cap; it can never create an entry."""
    if not panel:
        return {"multiplier": 1.0, "hard_block": False, "reason_codes": []}
    skeptic = panel.get("risk_skeptic", Decision("HOLD", 0, "missing skeptic", "fallback"))
    multiplier = {"none": 1.0, "mild": 0.75, "material": 0.50, "hard_block": 0.0}.get(skeptic.risk_severity, 0.50)
    odds = panel.get("trade_quality")
    if odds:
        if odds.probability_net_gain_over_fee < 0.45 or odds.expected_net_return_bucket == "below_minus_0.4":
            multiplier = min(multiplier, 0.50)
        elif odds.probability_net_gain_over_fee < 0.55:
            multiplier = min(multiplier, 0.75)
    reason_codes = []
    for decision in panel.values():
        for code in decision.reason_codes:
            if code not in reason_codes:
                reason_codes.append(code)
    return {"multiplier": multiplier, "hard_block": skeptic.risk_severity == "hard_block", "reason_codes": reason_codes[:12]}


def _analysis_audit(panel: dict[str, Decision]) -> dict:
    return {
        role: {
            "stance": decision.stance,
            "risk_severity": decision.risk_severity,
            "probability_net_gain_over_fee": decision.probability_net_gain_over_fee,
            "probability_material_loss": decision.probability_material_loss,
            "expected_net_return_bucket": decision.expected_net_return_bucket,
            "reason_codes": list(decision.reason_codes),
            "invalidation_codes": list(decision.invalidation_codes),
            "reason": decision.reason,
        }
        for role, decision in panel.items()
    }


def _exit_reason(feature: dict, state: PositionState, *, config: SwingV3Config) -> str | None:
    price = feature["price"]
    if price <= state.satellite_average_entry * (1 - config.initial_stop_sigma * state.satellite_entry_volatility):
        return "INITIAL_VOLATILITY_STOP"
    trail_active = state.max_close >= state.satellite_average_entry * (1 + config.trail_activation_sigma * state.satellite_entry_volatility)
    if trail_active and price <= state.max_close * (1 - config.trail_sigma * feature["volatility"]):
        return "TRAILING_VOLATILITY_STOP"
    if state.below_ema20_streak >= 2:
        return "TWO_CLOSES_BELOW_EMA20"
    if feature["ema20"] < feature["sma60"]:
        return "MEDIUM_TREND_FAILED"
    if state.held_sessions >= config.no_progress_sessions and price < state.satellite_average_entry * (1 + 0.25 * state.satellite_entry_volatility):
        return "NO_PROGRESS_DE_RISK"
    return None


def _buy_layer_to_target(state: PositionState, *, layer: str, price: float, target_weight: float) -> tuple[float, float]:
    """Buy one layer only enough to reach its capped portfolio weight."""
    if layer not in {"core", "satellite"}:
        raise ValueError("unknown position layer")
    total_value = state.cash + _total_grams(state) * price
    desired_value = total_value * min(1.0, max(0.0, target_weight))
    current_grams = state.core_grams if layer == "core" else state.satellite_grams
    amount = min(state.cash, max(0.0, desired_value - current_grams * price))
    if amount <= 1e-9:
        return 0.0, _position_weight(state, price)
    new_grams = amount / price
    if layer == "core":
        state.core_grams += new_grams
    else:
        total_grams = state.satellite_grams + new_grams
        state.satellite_average_entry = (state.satellite_average_entry * state.satellite_grams + price * new_grams) / total_grams
        state.satellite_grams = total_grams
    state.cash -= amount
    return amount, _position_weight(state, price)


def _sell_layer(state: PositionState, *, layer: str, price: float, sell_fee: float) -> tuple[float, float]:
    """Liquidate one layer at the next quote and apply the explicit sell fee."""
    grams = state.core_grams if layer == "core" else state.satellite_grams
    notional = grams * price
    fee = notional * sell_fee
    state.cash += notional - fee
    if layer == "core":
        state.core_grams = 0.0
    else:
        state.satellite_grams = 0.0
        state.satellite_average_entry = state.satellite_entry_volatility = state.max_close = 0.0
        state.held_sessions = state.add_stage = state.below_ema20_streak = 0
    return notional, fee


def replay(rows: list[dict], *, start: date, end: date, tool_url: str,
           use_deepseek: bool = True, panel_provider: PanelProvider | None = None,
           config: SwingV3Config = SwingV3Config()) -> dict:
    """Replay core plus satellite decisions, filled at the next observed quote."""
    if start > end:
        raise ValueError("start must not be after end")
    if len(rows) < config.warmup_sessions + 2:
        raise ValueError("not enough daily rows for swing-v3 warm-up and next-quote fills")
    state = PositionState(cash=INITIAL_CASH)
    trades, events, model_calls = [], [], 0
    first_signal_index: int | None = None
    for index in range(config.warmup_sessions, len(rows) - 1):
        row, fill = rows[index], rows[index + 1]
        signal_day = date.fromisoformat(row["observed_on"])
        if not start <= signal_day <= end:
            continue
        first_signal_index = first_signal_index if first_signal_index is not None else index
        history = rows[:index + 1]
        feature = features(history, config=config)
        candidate = entry_kind(feature, config=config)
        if state.core_grams:
            state.below_sma60_streak = state.below_sma60_streak + 1 if feature["price"] < feature["sma60"] else 0
        if state.satellite_grams:
            state.held_sessions += 1
            state.max_close = max(state.max_close, feature["price"])
            state.below_ema20_streak = state.below_ema20_streak + 1 if feature["price"] < feature["ema20"] else 0
        core_exit_reason = None
        if state.core_grams and (feature["ema20"] < feature["sma60"] or state.below_sma60_streak >= 2):
            core_exit_reason = "LONG_TREND_FAILED"
        satellite_exit_reason = _exit_reason(feature, state, config=config) if state.satellite_grams else None
        stage = "entry" if not state.satellite_grams else "add"
        add_kind = "none"
        if state.satellite_grams and not satellite_exit_reason and feature["uptrend"]:
            profitable = feature["price"] >= state.satellite_average_entry * (1 + config.add_profit_sigma * state.satellite_entry_volatility)
            if state.add_stage == 0 and profitable:
                add_kind = "add_one"
            elif state.add_stage == 1 and candidate == "breakout" and feature["macro_score"] >= 1:
                add_kind = "add_two"
        setup_exists = (not state.satellite_grams and candidate != "none") or add_kind != "none"
        panel: dict[str, Decision] = {}
        context: dict | None = None
        if setup_exists:
            context = analysis_context(history, feature, state, stage=stage, candidate=candidate, config=config)
            if panel_provider is not None:
                panel = panel_provider(history, context, state)
            elif use_deepseek:
                panel = _model_panel(history, feature, state, stage=stage, candidate=candidate, tool_url=tool_url, config=config)
            model_calls += len(panel)
        fusion = _fuse_panel(panel)
        macro_multiplier = _macro_multiplier(feature)
        actions: list[tuple[str, str, float]] = []
        if core_exit_reason:
            actions.append(("SELL_CORE", core_exit_reason, 0.0))
        elif not state.core_grams and feature["long_trend"]:
            actions.append(("BUY_CORE", "LONG_TREND_CORE_ALLOCATION", config.core_weight))
        if satellite_exit_reason:
            actions.append(("SELL_SATELLITE", satellite_exit_reason, 0.0))
        elif setup_exists and not fusion["hard_block"]:
            if not state.satellite_grams:
                actions.append(("BUY_SATELLITE", candidate.upper(), config.satellite_initial_weight * macro_multiplier * fusion["multiplier"]))
            elif add_kind == "add_one":
                actions.append(("ADD_SATELLITE_ONE", "PROFIT_CONFIRMED_TREND", min(config.satellite_initial_weight + config.satellite_add_one_weight,
                                                                                       config.satellite_initial_weight + config.satellite_add_one_weight * macro_multiplier * fusion["multiplier"])))
            elif add_kind == "add_two":
                actions.append(("ADD_SATELLITE_TWO", "BREAKOUT_AFTER_PROFIT", min(1.0 - config.core_weight,
                                                                                       config.satellite_initial_weight + config.satellite_add_one_weight + config.satellite_add_two_weight * fusion["multiplier"])))
        elif setup_exists and fusion["hard_block"]:
            actions.append(("HOLD", "MODEL_HARD_BLOCK", _position_weight(state, feature["price"])))
        fill_price = float(fill["price"])
        executed = []
        for action, reason, target_weight in actions:
            actual_notional, fee = 0.0, 0.0
            if action == "BUY_CORE":
                actual_notional, target_weight = _buy_layer_to_target(state, layer="core", price=fill_price, target_weight=target_weight)
            elif action in {"BUY_SATELLITE", "ADD_SATELLITE_ONE", "ADD_SATELLITE_TWO"}:
                actual_notional, target_weight = _buy_layer_to_target(state, layer="satellite", price=fill_price, target_weight=target_weight)
            if actual_notional > 0:
                if action == "BUY_SATELLITE":
                    state.satellite_entry_volatility, state.max_close = feature["volatility"], fill_price
                    state.held_sessions, state.add_stage, state.below_ema20_streak = 0, 0, 0
                elif action == "ADD_SATELLITE_ONE":
                    state.add_stage = 1
                elif action == "ADD_SATELLITE_TWO":
                    state.add_stage = 2
                trades.append({"action": action, "signal_day": row["observed_on"], "fill_day": fill["observed_on"],
                               "price": fill_price, "notional": round(actual_notional, 2), "fee": 0.0,
                               "target_weight_pct": round(target_weight * 100, 3), "reason": reason})
                executed.append(action)
            else:
                if action in {"SELL_CORE", "SELL_SATELLITE"}:
                    layer = "core" if action == "SELL_CORE" else "satellite"
                    actual_notional, fee = _sell_layer(state, layer=layer, price=fill_price, sell_fee=config.sell_fee)
                    trades.append({"action": action, "signal_day": row["observed_on"], "fill_day": fill["observed_on"],
                                   "price": fill_price, "notional": round(actual_notional, 2), "fee": round(fee, 2), "reason": reason})
                    executed.append(action)
        events.append({"signal_day": row["observed_on"], "fill_day": fill["observed_on"], "candidate": candidate,
                       "add_candidate": add_kind, "macro_score": feature["macro_score"], "macro_multiplier": macro_multiplier,
                       "analysis_context": context, "analyses": _analysis_audit(panel), "model_fusion": fusion,
                       "executed": executed or ["HOLD"], "reason": [item[1] for item in actions]})
    if first_signal_index is None:
        raise ValueError("no eligible signal sessions in the requested period")
    last_price = float(rows[-1]["price"])
    mark_to_market_value = state.cash + _total_grams(state) * last_price
    terminal_exit_fee = _total_grams(state) * last_price * config.sell_fee if _total_grams(state) else 0.0
    final_value = mark_to_market_value - terminal_exit_fee
    benchmark_entry = float(rows[first_signal_index + 1]["price"])
    buy_hold_value = INITIAL_CASH / benchmark_entry * last_price * (1 - config.sell_fee)
    realized_fees = sum(float(trade["fee"]) for trade in trades)
    return {
        "strategy": "swing_v3_core_satellite_volatility_targeted",
        "target_period": {"start": start.isoformat(), "end": end.isoformat()},
        "contract": {"signal": "daily close", "execution": "next observed daily quote", "buy_fee_rate": 0.0,
                     "sell_fee_rate": config.sell_fee, "terminal_valuation": "net liquidation at final observed quote"},
        "frozen_rule": {
            "core": "25% core allocation opens only in a rising long trend and exits only after long-trend failure",
            "satellite_entry": "price > EMA20 > SMA60 with positive EMA20 slope, then either 20-session breakout >0.25 sigma or pullback reclaim",
            "satellite_sizing": "35% initial satellite; adds only after a profitable 0.75-sigma move and then a supported breakout",
            "satellite_exit": "1.5-sigma initial stop, activated 2-sigma trailing stop, two closes below EMA20, trend failure, or 20-session no-progress de-risk",
            "model": "four specialised roles may cap size or hard-block only; no role can create an entry",
            "macro": "macro context multiplies target weight (50/75/100%) rather than vetoing a technical setup",
        },
        "initial_cash": INITIAL_CASH,
        "final_value": round(final_value, 2),
        "mark_to_market_value": round(mark_to_market_value, 2),
        "terminal_exit_fee": round(terminal_exit_fee, 2),
        "open_core_grams": round(state.core_grams, 8),
        "open_satellite_grams": round(state.satellite_grams, 8),
        "return_percent": round((final_value / INITIAL_CASH - 1) * 100, 3),
        "buy_and_hold_final_value": round(buy_hold_value, 2),
        "buy_and_hold_return_percent": round((buy_hold_value / INITIAL_CASH - 1) * 100, 3),
        "trade_count": len(trades),
        "realized_fees_paid": round(realized_fees, 2),
        "fees_paid": round(realized_fees + terminal_exit_fee, 2),
        "model_calls": model_calls,
        "trades": trades,
        "events": events,
        "limitations": [
            "This is a versioned research experiment, not a live trading path or a profit claim.",
            "Historical JD daily quotes were retrieved after the fact; their original availability timing remains unverified.",
            "Use an untouched future period for a final out-of-sample comparison; do not tune against that held-out period.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run isolated swing-v3 gold research replay.")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-deepseek", action="store_true")
    parser.add_argument("--tool-url", default="http://127.0.0.1:8000/api/v1/internal/research/gold-blind-decision")
    args = parser.parse_args()
    panel = collect_factor_panel(start=args.start - timedelta(days=120), end=args.end)
    result = replay(_daily_rows(panel), start=args.start, end=args.end, tool_url=args.tool_url, use_deepseek=not args.no_deepseek)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
