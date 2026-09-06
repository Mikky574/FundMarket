"""Fee-aware, point-in-time swing-v3 research replay for accumulated gold.

The deterministic layer finds trend breakouts and pullback resumptions, sizes
positions, and exits them. This module is intentionally separate from
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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from demos.gold_factor_lab.blind_replay import _daily_rows, _macro_context
from demos.gold_factor_lab.collector import collect_factor_panel


SELL_FEE = 0.004
INITIAL_CASH = 100_000.0


@dataclass(frozen=True)
class SwingV3Config:
    """Frozen, fee-aware strategy parameters for one replay version."""

    warmup_sessions: int = 65
    sell_fee: float = SELL_FEE
    core_weight: float = 0.25
    core_entry_valuation_z: float = 0.5
    core_trim_valuation_z: float = 1.5
    core_trim_fraction: float = 0.50
    value_initial_weight: float = 0.15
    value_add_weight: float = 0.15
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
    valuation_lookback: int = 120
    value_entry_z: float = -1.0
    value_add_z: float = -1.6
    value_reduce_z: float = 1.5
    downside_shock_sigma: float = 1.75
    shock_cooldown_sessions: int = 3
    minimum_trade_notional: float = 1_000.0
    satellite_add_max_valuation_z: float = float("inf")
    strategy_name: str = "swing_v3_core_satellite_volatility_targeted"


@dataclass(frozen=True)
class SwingV4Config(SwingV3Config):
    """Lower-turnover satellite variant; v3 remains reproducible unchanged."""

    satellite_initial_weight: float = 0.25
    satellite_add_one_weight: float = 0.15
    satellite_add_two_weight: float = 0.0
    satellite_add_max_valuation_z: float = 1.0
    strategy_name: str = "swing_v4_fee_aware_valuation_capped_satellite"


@dataclass
class PositionState:
    cash: float
    core_grams: float = 0.0
    value_grams: float = 0.0
    satellite_grams: float = 0.0
    value_average_entry: float = 0.0
    satellite_average_entry: float = 0.0
    satellite_entry_volatility: float = 0.0
    max_close: float = 0.0
    held_sessions: int = 0
    add_stage: int = 0
    below_ema20_streak: int = 0
    below_sma60_streak: int = 0
    core_trimmed: bool = False


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
    required_history = max(config.warmup_sessions, config.valuation_lookback)
    if len(prices) < required_history:
        raise ValueError("swing-v3 requires its configured warm-up history")
    price = prices[-1]
    ema5, ema20, sma60 = _ema(prices, 5), _ema(prices, 20), _sma(prices, 60)
    prior_ema5, ema20_five_sessions_ago = _ema(prices[:-1], 5), _ema(prices[:-5], 20)
    sma60_five_sessions_ago = _sma(prices[:-5], 60)
    volatility = _realized_volatility(prices, period=20, floor=config.volatility_floor)
    assert ema5 is not None and ema20 is not None and sma60 is not None
    assert prior_ema5 is not None and ema20_five_sessions_ago is not None and sma60_five_sessions_ago is not None and volatility is not None
    resistance20, support20 = max(prices[-21:-1]), min(prices[-21:-1])
    valuation_center = statistics.median(prices[-config.valuation_lookback:])
    valuation_z = (price / valuation_center - 1) / volatility
    recent_returns = [prices[index] / prices[index - 1] - 1 for index in range(max(1, len(prices) - config.shock_cooldown_sessions), len(prices))]
    downside_shock_active = any(change <= -config.downside_shock_sigma * volatility for change in recent_returns)
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
        "valuation_center": valuation_center,
        "valuation_z": valuation_z,
        "high10": max(prices[-11:-1]),
        "low5": min(prices[-6:-1]),
        "previous_price": prices[-2],
        "short_trend_recovered": price > ema5 and prices[-2] > prior_ema5 and ema5 >= prior_ema5,
        "downside_shock_active": downside_shock_active,
        "uptrend": price > ema20 > sma60 and ema20 > ema20_five_sessions_ago,
        "long_trend": price > sma60 and ema20 > sma60 and sma60 >= sma60_five_sessions_ago,
        "macro_score": macro_score,
        "macro_available": macro_available,
        "oil_risk": oil_risk,
        "macro_labels": macro_labels,
    }


def entry_kind(feature: dict, *, config: SwingV3Config = SwingV3Config()) -> str:
    """Return a deterministic entry setup, never a model-created signal."""
    if not feature["uptrend"] or not feature["short_trend_recovered"] or feature["downside_shock_active"]:
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


def _layer_weight(state: PositionState, layer: str, price: float) -> float:
    grams = {"core": state.core_grams, "value": state.value_grams, "satellite": state.satellite_grams}[layer]
    value = state.cash + _total_grams(state) * price
    return 0.0 if value <= 0 else grams * price / value


def _total_grams(state: PositionState) -> float:
    return state.core_grams + state.value_grams + state.satellite_grams


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


def _buy_layer_to_target(state: PositionState, *, layer: str, price: float, target_weight: float,
                         minimum_notional: float) -> tuple[float, float]:
    """Buy one layer only enough to reach its capped portfolio weight."""
    if layer not in {"core", "value", "satellite"}:
        raise ValueError("unknown position layer")
    total_value = state.cash + _total_grams(state) * price
    desired_value = total_value * min(1.0, max(0.0, target_weight))
    current_grams = {"core": state.core_grams, "value": state.value_grams, "satellite": state.satellite_grams}[layer]
    amount = min(state.cash, max(0.0, desired_value - current_grams * price))
    if amount < minimum_notional:
        return 0.0, _position_weight(state, price)
    new_grams = amount / price
    if layer == "core":
        state.core_grams += new_grams
    elif layer == "value":
        total_grams = state.value_grams + new_grams
        state.value_average_entry = (state.value_average_entry * state.value_grams + price * new_grams) / total_grams
        state.value_grams = total_grams
    else:
        total_grams = state.satellite_grams + new_grams
        state.satellite_average_entry = (state.satellite_average_entry * state.satellite_grams + price * new_grams) / total_grams
        state.satellite_grams = total_grams
    state.cash -= amount
    return amount, _position_weight(state, price)


def _sell_layer(state: PositionState, *, layer: str, price: float, sell_fee: float) -> tuple[float, float]:
    """Liquidate one layer at the next quote and apply the explicit sell fee."""
    grams = {"core": state.core_grams, "value": state.value_grams, "satellite": state.satellite_grams}[layer]
    notional = grams * price
    fee = notional * sell_fee
    state.cash += notional - fee
    if layer == "core":
        state.core_grams = 0.0
        state.core_trimmed = False
    elif layer == "value":
        state.value_grams = state.value_average_entry = 0.0
    else:
        state.satellite_grams = 0.0
        state.satellite_average_entry = state.satellite_entry_volatility = state.max_close = 0.0
        state.held_sessions = state.add_stage = state.below_ema20_streak = 0
    return notional, fee


def _trim_core(state: PositionState, *, price: float, fraction: float, sell_fee: float) -> tuple[float, float]:
    """Realise part of a mature core position while preserving trend exposure."""
    grams = state.core_grams * min(1.0, max(0.0, fraction))
    notional = grams * price
    fee = notional * sell_fee
    state.core_grams -= grams
    state.cash += notional - fee
    state.core_trimmed = True
    return notional, fee


def _promote_value_to_core(state: PositionState, *, price: float, target_weight: float) -> tuple[float, float]:
    """Reclassify already-owned low-valuation gold as core without trading it."""
    total_value = state.cash + _total_grams(state) * price
    target_grams = total_value * target_weight / price
    grams = min(state.value_grams, max(0.0, target_grams - state.core_grams))
    if grams <= 1e-9:
        return 0.0, 0.0
    state.value_grams -= grams
    state.core_grams += grams
    return grams, grams * price


def replay(rows: list[dict], *, start: date, end: date,
           config: SwingV3Config = SwingV3Config()) -> dict:
    """Replay deterministic core plus satellite decisions, filled at the next quote."""
    if start > end:
        raise ValueError("start must not be after end")
    if len(rows) < config.warmup_sessions + 2:
        raise ValueError("not enough daily rows for swing-v3 warm-up and next-quote fills")
    eligible_row_indexes = [
        index for index, row in enumerate(rows)
        if date.fromisoformat(row["observed_on"]) <= end
    ]
    if not eligible_row_indexes:
        raise ValueError("no daily rows are available on or before the requested end date")
    last_evaluation_index = eligible_row_indexes[-1]
    state = PositionState(cash=INITIAL_CASH)
    trades, events = [], []
    first_signal_index: int | None = None
    for index in range(config.warmup_sessions, min(len(rows) - 1, last_evaluation_index)):
        row, fill = rows[index], rows[index + 1]
        signal_day = date.fromisoformat(row["observed_on"])
        if not start <= signal_day <= end:
            continue
        first_signal_index = first_signal_index if first_signal_index is not None else index
        history = rows[:index + 1]
        feature = features(history, config=config)
        candidate = entry_kind(feature, config=config)
        value_entry = (not state.value_grams and feature["valuation_z"] <= config.value_entry_z and
                       feature["short_trend_recovered"] and not feature["downside_shock_active"])
        value_add = (state.value_grams and feature["valuation_z"] <= config.value_add_z and
                     feature["price"] < state.value_average_entry and feature["short_trend_recovered"] and
                     not feature["downside_shock_active"])
        value_exit = (state.value_grams and feature["valuation_z"] >= config.value_reduce_z and
                      feature["price"] < feature["ema5"])
        core_entry = (not state.core_grams and feature["long_trend"] and not feature["downside_shock_active"] and
                      feature["valuation_z"] <= config.core_entry_valuation_z)
        if state.core_grams:
            state.below_sma60_streak = state.below_sma60_streak + 1 if feature["price"] < feature["sma60"] else 0
        if state.satellite_grams:
            state.held_sessions += 1
            state.max_close = max(state.max_close, feature["price"])
            state.below_ema20_streak = state.below_ema20_streak + 1 if feature["price"] < feature["ema20"] else 0
        core_exit_reason = None
        if state.core_grams and (feature["ema20"] < feature["sma60"] or state.below_sma60_streak >= 2):
            core_exit_reason = "LONG_TREND_FAILED"
        core_trim_reason = None
        if state.core_grams and not core_exit_reason and not state.core_trimmed and feature["valuation_z"] >= config.core_trim_valuation_z and feature["price"] < feature["ema5"]:
            core_trim_reason = "VALUATION_PREMIUM_AND_SHORT_TREND_WEAKNESS"
        satellite_exit_reason = _exit_reason(feature, state, config=config) if state.satellite_grams else None
        promoted_grams, promoted_notional = (0.0, 0.0)
        if not core_exit_reason and not value_exit and not state.core_grams and state.value_grams and feature["long_trend"]:
            promoted_grams, promoted_notional = _promote_value_to_core(state, price=feature["price"], target_weight=config.core_weight)
        stage = "entry" if not state.satellite_grams else "add"
        add_kind = "none"
        if state.satellite_grams and not satellite_exit_reason and feature["uptrend"]:
            profitable = feature["price"] >= state.satellite_average_entry * (1 + config.add_profit_sigma * state.satellite_entry_volatility)
            if state.add_stage == 0 and profitable and feature["valuation_z"] <= config.satellite_add_max_valuation_z:
                add_kind = "add_one"
            elif state.add_stage == 1 and config.satellite_add_two_weight > 0 and candidate == "breakout" and feature["macro_score"] >= 1:
                add_kind = "add_two"
        satellite_setup = (not state.satellite_grams and candidate != "none") or add_kind != "none"
        macro_multiplier = _macro_multiplier(feature)
        actions: list[tuple[str, str, float]] = []
        if core_exit_reason:
            actions.append(("SELL_CORE", core_exit_reason, 0.0))
        elif core_trim_reason:
            actions.append(("TRIM_CORE", core_trim_reason, config.core_trim_fraction))
        elif core_entry:
            actions.append(("BUY_CORE", "LONG_TREND_CORE_ALLOCATION", config.core_weight))
        if value_exit:
            actions.append(("SELL_VALUE", "VALUATION_PREMIUM_AND_SHORT_TREND_WEAKNESS", 0.0))
        elif value_entry:
            actions.append(("BUY_VALUE", "VALUATION_DISCOUNT_STABILISED", config.value_initial_weight))
        elif value_add:
            actions.append(("ADD_VALUE", "DEEPER_VALUATION_DISCOUNT_STABILISED", config.value_initial_weight + config.value_add_weight))
        if satellite_exit_reason:
            actions.append(("SELL_SATELLITE", satellite_exit_reason, 0.0))
        elif satellite_setup:
            if not state.satellite_grams:
                actions.append(("BUY_SATELLITE", candidate.upper(), config.satellite_initial_weight * macro_multiplier))
            elif add_kind == "add_one":
                actions.append(("ADD_SATELLITE_ONE", "PROFIT_CONFIRMED_TREND", min(config.satellite_initial_weight + config.satellite_add_one_weight,
                                                                                       config.satellite_initial_weight + config.satellite_add_one_weight * macro_multiplier)))
            elif add_kind == "add_two":
                satellite_cap = max(0.0, 1.0 - config.core_weight - _layer_weight(state, "value", feature["price"]))
                actions.append(("ADD_SATELLITE_TWO", "BREAKOUT_AFTER_PROFIT", min(1.0 - config.core_weight,
                                                                                       satellite_cap, config.satellite_initial_weight + config.satellite_add_one_weight + config.satellite_add_two_weight)))
        fill_price = float(fill["price"])
        executed = []
        for action, reason, target_weight in actions:
            actual_notional, fee = 0.0, 0.0
            if action == "BUY_CORE":
                actual_notional, target_weight = _buy_layer_to_target(state, layer="core", price=fill_price, target_weight=target_weight, minimum_notional=config.minimum_trade_notional)
            elif action in {"BUY_VALUE", "ADD_VALUE"}:
                actual_notional, target_weight = _buy_layer_to_target(state, layer="value", price=fill_price, target_weight=target_weight, minimum_notional=config.minimum_trade_notional)
            elif action in {"BUY_SATELLITE", "ADD_SATELLITE_ONE", "ADD_SATELLITE_TWO"}:
                actual_notional, target_weight = _buy_layer_to_target(state, layer="satellite", price=fill_price, target_weight=target_weight, minimum_notional=config.minimum_trade_notional)
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
                               "portfolio_weight_pct": round(target_weight * 100, 3), "reason": reason})
                executed.append(action)
            else:
                if action == "TRIM_CORE":
                    actual_notional, fee = _trim_core(state, price=fill_price, fraction=target_weight, sell_fee=config.sell_fee)
                    trades.append({"action": action, "signal_day": row["observed_on"], "fill_day": fill["observed_on"],
                                   "price": fill_price, "notional": round(actual_notional, 2), "fee": round(fee, 2), "reason": reason})
                    executed.append(action)
                elif action in {"SELL_CORE", "SELL_VALUE", "SELL_SATELLITE"}:
                    layer = {"SELL_CORE": "core", "SELL_VALUE": "value", "SELL_SATELLITE": "satellite"}[action]
                    actual_notional, fee = _sell_layer(state, layer=layer, price=fill_price, sell_fee=config.sell_fee)
                    trades.append({"action": action, "signal_day": row["observed_on"], "fill_day": fill["observed_on"],
                                   "price": fill_price, "notional": round(actual_notional, 2), "fee": round(fee, 2), "reason": reason})
                    executed.append(action)
        events.append({"signal_day": row["observed_on"], "fill_day": fill["observed_on"], "candidate": candidate,
                       "add_candidate": add_kind, "valuation_center": round(feature["valuation_center"], 4), "valuation_z": round(feature["valuation_z"], 3),
                       "macro_score": feature["macro_score"], "macro_multiplier": macro_multiplier,
                       "value_to_core_reclassification": {"grams": round(promoted_grams, 8), "notional": round(promoted_notional, 2)},
                       "executed": executed or ["HOLD"], "reason": [item[1] for item in actions]})
    if first_signal_index is None:
        raise ValueError("no eligible signal sessions in the requested period")
    last_price = float(rows[last_evaluation_index]["price"])
    mark_to_market_value = state.cash + _total_grams(state) * last_price
    # The research period ending is not a trade instruction. Open gold is
    # therefore carried at its observed market value without inventing a sale
    # or charging a hypothetical redemption fee.
    terminal_exit_fee = 0.0
    final_value = mark_to_market_value
    benchmark_entry = float(rows[first_signal_index + 1]["price"])
    buy_hold_value = INITIAL_CASH / benchmark_entry * last_price
    realized_fees = sum(float(trade["fee"]) for trade in trades)
    return {
        "strategy": config.strategy_name,
        "target_period": {"start": start.isoformat(), "end": end.isoformat()},
        "contract": {"signal": "daily close", "execution": "next observed daily quote", "buy_fee_rate": 0.0,
                     "sell_fee_rate": config.sell_fee, "terminal_valuation": "mark-to-market at final observed quote; no hypothetical sale"},
        "frozen_rule": {
            "core": "25% core allocation is built from existing low-valuation inventory after long-trend confirmation; cash may create it only at or below a modest valuation premium, one half may be realised at an extended premium plus short-trend weakness, and the remainder exits only after long-trend failure",
            "value": "15% value allocation opens after a stabilised discount below a 120-session median; one 15% add only at a deeper discount; it exits only when premium and short-trend weakness coincide",
            "trend_safety": "a downside shock of 1.75 times 20-session volatility starts a three-session no-entry cooldown; after that, the latest two closes must both be above their EMA5 and EMA5 must be rising",
            "satellite_entry": "after the trend-safety gate, price > EMA20 > SMA60 with positive EMA20 slope, then either 20-session breakout >0.25 sigma or pullback reclaim",
            "satellite_sizing": f"{config.satellite_initial_weight:.0%} initial satellite; first add is capped at {config.satellite_add_one_weight:.0%}; second add is {config.satellite_add_two_weight:.0%}; new adds require valuation z <= {config.satellite_add_max_valuation_z}",
            "satellite_exit": "1.5-sigma initial stop, activated 2-sigma trailing stop, two closes below EMA20, trend failure, or 20-session no-progress de-risk",
            "model": "DeepSeek is explanation-only; it cannot influence entries, exits, or position sizing",
            "macro": "macro context multiplies target weight (50/75/100%) rather than vetoing a technical setup",
        },
        "initial_cash": INITIAL_CASH,
        "final_value": round(final_value, 2),
        "mark_to_market_value": round(mark_to_market_value, 2),
        "terminal_exit_fee": 0.0,
        "open_core_grams": round(state.core_grams, 8),
        "open_value_grams": round(state.value_grams, 8),
        "open_satellite_grams": round(state.satellite_grams, 8),
        "return_percent": round((final_value / INITIAL_CASH - 1) * 100, 3),
        "buy_and_hold_final_value": round(buy_hold_value, 2),
        "buy_and_hold_return_percent": round((buy_hold_value / INITIAL_CASH - 1) * 100, 3),
        "trade_count": len(trades),
        "realized_fees_paid": round(realized_fees, 2),
        "fees_paid": round(realized_fees, 2),
        "trades": trades,
        "events": events,
        "limitations": [
            "This is a versioned research experiment, not a live trading path or a profit claim.",
            "Historical JD daily quotes were retrieved after the fact; their original availability timing remains unverified.",
            "Use an untouched future period for a final out-of-sample comparison; do not tune against that held-out period.",
        ],
    }


def replay_v4(rows: list[dict], *, start: date, end: date) -> dict:
    """Run the frozen v4 fee-aware satellite configuration."""
    return replay(rows, start=start, end=end, config=SwingV4Config())


def main() -> None:
    parser = argparse.ArgumentParser(description="Run isolated swing-v3 gold research replay.")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    panel = collect_factor_panel(start=args.start - timedelta(days=400), end=args.end)
    result = replay(_daily_rows(panel), start=args.start, end=args.end)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
