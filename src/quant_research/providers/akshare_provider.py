from datetime import date, datetime, timedelta

import akshare as ak
import pandas as pd

from src.quant_research.models import Candle, Quote, StockItem
from src.quant_research.providers.base import StockProvider


def _number(value):
    if value is None or pd.isna(value):
        return None
    return float(value)


def exchange_of(symbol: str) -> str:
    if symbol.startswith(("4", "8", "92")):
        return "BJ"
    if symbol.startswith(("5", "6", "9")):
        return "SH"
    return "SZ"


class AkShareProvider(StockProvider):
    """AkShare 适配层。上层接口不依赖 AkShare 的中文列名。"""

    def list_stocks(self) -> list[StockItem]:
        # 搜索只需要稳定的代码/名称目录，不应依赖容易限流的全市场实时行情。
        frame = ak.stock_info_a_code_name()
        code_column = "code" if "code" in frame.columns else "代码"
        name_column = "name" if "name" in frame.columns else "名称"
        return [
            StockItem(
                symbol=str(row[code_column]).zfill(6),
                exchange=exchange_of(str(row[code_column]).zfill(6)),
                name=str(row[name_column]),
            )
            for _, row in frame.iterrows()
        ]

    def get_quote(self, symbol: str) -> Quote | None:
        try:
            frame = ak.stock_zh_a_spot_em()
            rows = frame[frame["代码"].astype(str).str.zfill(6) == symbol]
            if rows.empty:
                return None
            row = rows.iloc[0]
            return Quote(
                symbol=symbol, exchange=exchange_of(symbol), name=str(row["名称"]),
                price=_number(row.get("最新价")), change=_number(row.get("涨跌额")),
                change_percent=_number(row.get("涨跌幅")), open=_number(row.get("今开")),
                high=_number(row.get("最高")), low=_number(row.get("最低")),
                previous_close=_number(row.get("昨收")), volume=_number(row.get("成交量")),
                amount=_number(row.get("成交额")), turnover_rate=_number(row.get("换手率")),
                timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
            )
        except Exception:
            # 东方财富不可用时，使用新浪最近两个交易日合成最新状态。
            end = date.today()
            start = end - timedelta(days=370)
            rows = self._sina_history(symbol, start, end, "daily", "qfq")
            if not rows:
                return None
            latest, previous = rows[-1], rows[-2] if len(rows) > 1 else rows[-1]
            change = latest.close - previous.close
            return Quote(
                symbol=symbol, exchange=exchange_of(symbol), name=f"股票 {symbol}",
                price=latest.close, change=change,
                change_percent=(change / previous.close * 100) if previous.close else 0,
                open=latest.open, high=latest.high, low=latest.low,
                previous_close=previous.close, volume=latest.volume, amount=latest.amount,
                turnover_rate=latest.turnover_rate,
                timestamp=f"{latest.date.isoformat()}T15:00:00",
            )

    def get_history(self, symbol: str, start: date, end: date, period: str, adjust: str) -> list[Candle]:
        try:
            frame = ak.stock_zh_a_hist(symbol=symbol, period=period, start_date=start.strftime("%Y%m%d"),
                                       end_date=end.strftime("%Y%m%d"), adjust=adjust)
            return [Candle(date=pd.to_datetime(row["日期"]).date(), open=float(row["开盘"]),
                           high=float(row["最高"]), low=float(row["最低"]), close=float(row["收盘"]),
                           volume=float(row["成交量"]), amount=_number(row.get("成交额")),
                           amplitude=_number(row.get("振幅")), change_percent=_number(row.get("涨跌幅")),
                           change=_number(row.get("涨跌额")), turnover_rate=_number(row.get("换手率")))
                    for _, row in frame.iterrows()]
        except Exception:
            return self._sina_history(symbol, start, end, period, adjust)

    @staticmethod
    def _sina_history(symbol: str, start: date, end: date, period: str, adjust: str) -> list[Candle]:
        prefix = "sh" if exchange_of(symbol) == "SH" else "bj" if exchange_of(symbol) == "BJ" else "sz"
        frame = ak.stock_zh_a_daily(symbol=f"{prefix}{symbol}", start_date=start.strftime("%Y%m%d"),
                                    end_date=end.strftime("%Y%m%d"), adjust=adjust)
        if frame.empty:
            return []
        frame = frame.copy()
        frame["date"] = pd.to_datetime(frame["date"])
        if period in {"weekly", "monthly"}:
            rule = "W-FRI" if period == "weekly" else "ME"
            frame = frame.set_index("date").resample(rule).agg(
                {"open": "first", "high": "max", "low": "min", "close": "last",
                 "volume": "sum", "amount": "sum", "turnover": "sum"}
            ).dropna(subset=["close"]).reset_index()
        result = []
        previous_close = None
        for _, row in frame.iterrows():
            close = float(row["close"])
            change = None if previous_close is None else close - previous_close
            change_percent = None if previous_close in (None, 0) else change / previous_close * 100
            turnover = _number(row.get("turnover"))
            result.append(Candle(
                date=pd.to_datetime(row["date"]).date(), open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=close, volume=float(row["volume"]), amount=_number(row.get("amount")),
                amplitude=(float(row["high"]) - float(row["low"])) / previous_close * 100 if previous_close else None,
                change_percent=change_percent, change=change,
                turnover_rate=turnover * 100 if turnover is not None else None,
            ))
            previous_close = close
        return result
