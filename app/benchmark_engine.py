from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import akshare as ak
import pandas as pd


FOUR = Decimal("0.0001")


def q4(value) -> Decimal:
    return Decimal(str(value)).quantize(FOUR, rounding=ROUND_HALF_UP)


def calculate_record(as_of: str, bases: dict, values: dict, portfolio_return: Decimal | None = None) -> dict:
    csi_return = (Decimal(str(values["csi300"])) / Decimal(str(bases["csi300"])) - 1) * 100
    industry_return = (Decimal(str(values["industry"])) / Decimal(str(bases["industry"])) - 1) * 100
    fund_return = (Decimal(str(values["fund_nav"])) / Decimal(str(bases["fund_nav"])) - 1) * 100
    balanced = csi_return * Decimal("0.5")
    result = {
        "date": as_of, "values": {k: str(v) for k, v in values.items()},
        "returns_percent": {"csi300": str(q4(csi_return)), "cash_50_csi300_50": str(q4(balanced)),
                            "industry": str(q4(industry_return)), "fund_buy_hold": str(q4(fund_return))},
    }
    if portfolio_return is not None:
        result["portfolio_return_percent"] = str(q4(portfolio_return))
        result["excess_percent"] = {
            "vs_cash_csi300": str(q4(portfolio_return - balanced)),
            "vs_industry": str(q4(portfolio_return - industry_return)),
            "vs_fund_buy_hold": str(q4(portfolio_return - fund_return)),
        }
    return result


class BenchmarkTracker:
    def __init__(self, path: Path):
        self.path = path
        self.state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _official_values(as_of: str, fund_code: str, industry_name: str) -> dict:
        compact = as_of.replace("-", "")
        csi = ak.index_zh_a_hist("000300", start_date=compact, end_date=compact)
        industry = ak.stock_board_industry_hist_em(industry_name, start_date=compact, end_date=compact, period="日k", adjust="")
        fund = ak.fund_open_fund_info_em(fund_code, "单位净值走势")
        fund_dates = pd.to_datetime(fund["净值日期"]).dt.date.astype(str)
        fund_row = fund[fund_dates == as_of]
        if csi.empty or industry.empty or fund_row.empty:
            raise ValueError(f"{as_of} 的某项正式基准数据尚未发布")
        return {"csi300": float(csi.iloc[-1]["收盘"]), "industry": float(industry.iloc[-1]["收盘"]),
                "fund_nav": float(fund_row.iloc[-1]["单位净值"])}

    def initialize(self, as_of: str, fund_code: str, fund_name: str, industry_name: str):
        if self.state is not None:
            raise ValueError("基准已经初始化")
        if as_of > date.today().isoformat():
            raise ValueError("禁止用未来日期初始化基准")
        values = self._official_values(as_of, fund_code, industry_name)
        self.state = {"version": 1, "start_date": as_of, "fund_code": fund_code, "fund_name": fund_name,
                      "industry_name": industry_name, "bases": values, "records": []}
        self.state["records"].append(calculate_record(as_of, values, values, Decimal("0")))
        self.save()

    def update(self, as_of: str, portfolio_state_path: Path) -> dict:
        if as_of > date.today().isoformat():
            raise ValueError("禁止更新未来日期")
        existing = [x for x in self.state["records"] if x["date"] == as_of]
        if existing:
            return existing[-1]
        portfolio = json.loads(portfolio_state_path.read_text(encoding="utf-8"))
        matches = [x for x in portfolio.get("valuations", []) if x["date"] == as_of]
        if not matches:
            raise ValueError("应先完成同日组合估值")
        values = self._official_values(as_of, self.state["fund_code"], self.state["industry_name"])
        record = calculate_record(as_of, self.state["bases"], values, Decimal(matches[-1]["return_percent"]))
        self.state["records"].append(record)
        self.save()
        return record
