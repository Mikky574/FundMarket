import re
from datetime import date

from cachetools import TTLCache

from app.config import settings
from app.models import Candle, Quote, StockItem
from app.market_store import get_aligned_cache, get_cache, put_cache, record_search
from app.providers.base import StockProvider


SYMBOL_PATTERN = re.compile(r"^(?:(SH|SZ|BJ)[.:-]?)?(\d{6})$", re.IGNORECASE)


def normalize_symbol(value: str) -> str:
    match = SYMBOL_PATTERN.fullmatch(value.strip())
    if not match:
        raise ValueError("股票代码格式错误，请使用 600000、SH600000 或 SH.600000")
    return match.group(2)


class StockService:
    def __init__(self, provider: StockProvider):
        self.provider = provider
        self.market_cache = TTLCache(maxsize=1, ttl=settings.cache_ttl_seconds)
        self.quote_cache = TTLCache(maxsize=2048, ttl=settings.cache_ttl_seconds)
        self.history_cache = TTLCache(maxsize=512, ttl=settings.history_cache_ttl_seconds)

    def stocks(self, query: str | None = None, limit: int = 50, offset: int = 0) -> list[StockItem]:
        if "all" not in self.market_cache:
            catalog_key = f"stocks:catalog:{type(self.provider).__module__}.{type(self.provider).__name__}"
            cached, fresh = get_cache(catalog_key, 24 * 60 * 60)
            if cached is not None and fresh:
                self.market_cache["all"] = [StockItem.model_validate(x) for x in cached]
            else:
                try:
                    fetched = self.provider.list_stocks()
                    self.market_cache["all"] = fetched
                    put_cache(catalog_key, "stock_catalog", [x.model_dump() for x in fetched])
                except Exception:
                    if cached is None:
                        raise
                    self.market_cache["all"] = [StockItem.model_validate(x) for x in cached]
        items = self.market_cache["all"]
        if query:
            q = query.strip().lower()
            record_search("stock", query)
            items = [x for x in items if q in x.symbol.lower() or q in x.name.lower()]
        return items[offset:offset + limit]

    def stock_count(self, query: str | None = None) -> int:
        return len(self.stocks(query, 100_000, 0))

    def quote(self, raw_symbol: str) -> Quote | None:
        symbol = normalize_symbol(raw_symbol)
        if symbol not in self.quote_cache:
            key = f"stock:quote:{symbol}"
            cached, fresh = get_aligned_cache(key, settings.market_refresh_seconds)
            if cached is not None and fresh:
                result = Quote.model_validate(cached)
            else:
                try:
                    result = self.provider.get_quote(symbol)
                    if result is not None:
                        put_cache(key, "stock_quote", result.model_dump(mode="json"))
                except Exception:
                    if cached is None:
                        raise
                    result = Quote.model_validate(cached)
            if result is None:
                return None
            if result.name == f"股票 {symbol}":
                # 即使用户直接打开详情，也从持久化股票目录补齐名称。
                matches = self.stocks(symbol, 1)
                item = matches[0] if matches else None
                if item:
                    result = result.model_copy(update={"name": item.name})
            self.quote_cache[symbol] = result
        return self.quote_cache[symbol]

    def history(self, raw_symbol: str, start: date, end: date, period: str, adjust: str) -> list[Candle]:
        symbol = normalize_symbol(raw_symbol)
        key = (symbol, start.isoformat(), end.isoformat(), period, adjust)
        if key not in self.history_cache:
            disk_key = f"stock:history:{symbol}:{start}:{end}:{period}:{adjust}"
            cached, fresh = get_aligned_cache(disk_key, settings.market_refresh_seconds)
            if cached is not None and fresh:
                rows = [Candle.model_validate(x) for x in cached]
            else:
                try:
                    rows = self.provider.get_history(symbol, start, end, period, adjust)
                    put_cache(disk_key, "stock_history", [x.model_dump(mode="json") for x in rows])
                except Exception:
                    if cached is None:
                        raise
                    rows = [Candle.model_validate(x) for x in cached]
            self.history_cache[key] = rows
        return self.history_cache[key]
