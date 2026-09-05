"""Strict contracts for non-persistent quantitative research capabilities.

These models deliberately contain feature values only.  Calendar dates,
future labels, raw news and ledger identifiers are not part of this contract,
so a research caller cannot accidentally reintroduce them into a blind replay.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


SessionCount = Annotated[int, Field(ge=1, le=20)]


class BlindGoldAnalysisContext(BaseModel):
    """Feature-only context accepted by the swing-v3 gold research panel."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["swing_v3"] = "swing_v3"
    sequence: int = Field(ge=1, le=1_000_000)
    candidate_stage: Literal["entry", "add", "exit_review"]
    requested_horizon_sessions: list[SessionCount] = Field(min_length=1, max_length=2)
    current_weight_pct: float = Field(ge=0, le=100)
    held_sessions: int = Field(ge=0, le=1000)
    buy_fee_pct: float = Field(ge=0, le=5)
    sell_fee_pct: float = Field(ge=0, le=5)
    max_factor_age_sessions: int = Field(ge=0, le=20)
    candidate_kind: Literal["breakout", "pullback", "none"] = "none"
    realized_volatility_20d_pct: float | None = Field(default=None, ge=0, le=100)
    price_vs_ema5_pct: float | None = Field(default=None, ge=-100, le=100)
    price_vs_ema20_pct: float | None = Field(default=None, ge=-100, le=100)
    price_vs_sma60_pct: float | None = Field(default=None, ge=-100, le=100)
    ema20_slope_5d_pct: float | None = Field(default=None, ge=-100, le=100)
    distance_to_resistance20_pct: float | None = Field(default=None, ge=-100, le=100)
    distance_to_support20_pct: float | None = Field(default=None, ge=-100, le=100)
    macro_score: int = Field(ge=-4, le=4)
    macro_available: int = Field(ge=0, le=4)
