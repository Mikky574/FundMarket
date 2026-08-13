from abc import ABC, abstractmethod
from datetime import date

from app.models import Candle, Quote, StockItem


class StockProvider(ABC):
    @abstractmethod
    def list_stocks(self) -> list[StockItem]: ...

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote | None: ...

    @abstractmethod
    def get_history(
        self, symbol: str, start: date, end: date, period: str, adjust: str
    ) -> list[Candle]: ...

