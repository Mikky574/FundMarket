from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class StockItem(BaseModel):
    symbol: str
    exchange: Literal["SH", "SZ", "BJ"]
    name: str


class Quote(StockItem):
    price: float | None = None
    change: float | None = None
    change_percent: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    previous_close: float | None = None
    volume: float | None = None
    amount: float | None = None
    turnover_rate: float | None = None
    timestamp: str | None = None


class Candle(BaseModel):
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float | None = None
    amplitude: float | None = None
    change_percent: float | None = None
    change: float | None = None
    turnover_rate: float | None = None


class HistoryResponse(BaseModel):
    symbol: str
    period: Literal["daily", "weekly", "monthly"]
    adjust: Literal["", "qfq", "hfq"]
    data: list[Candle]


class ErrorResponse(BaseModel):
    detail: str = Field(examples=["股票 600000 不存在"])

