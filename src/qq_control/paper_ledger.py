from __future__ import annotations

import json
import hashlib
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import akshare as ak
import pandas as pd


MONEY = Decimal("0.01")
SHARES = Decimal("0.0001")


def qmoney(value) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def qshares(value) -> Decimal:
    return Decimal(str(value)).quantize(SHARES, rounding=ROUND_HALF_UP)


def order_schedule_after_cutoff(decision_date: str, trading_dates: list[str]) -> tuple[str, str]:
    """Return NAV date and normal confirmation date for an order submitted after 15:00."""
    days = sorted(set(x for x in trading_dates if x > decision_date))
    if len(days) < 2:
        raise ValueError("交易日历不足，无法计算净值日和确认日")
    return days[0], days[1]


class PaperLedger:
    def __init__(self, path: Path):
        self.path = path
        self.state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def save(self, event: str | None = None, payload: dict | None = None):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        if event:
            self._audit(event, payload or {})

    def _audit(self, event: str, payload: dict):
        audit_path = self.path.with_name(f"{self.path.stem}.audit.jsonl")
        previous_hash = "GENESIS"
        if audit_path.exists():
            lines = [x for x in audit_path.read_text(encoding="utf-8").splitlines() if x.strip()]
            if lines:
                previous_hash = json.loads(lines[-1])["hash"]
        state_canonical = json.dumps(self.state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        record = {"timestamp": datetime.now().astimezone().isoformat(timespec="seconds"), "event": event,
                  "payload": payload, "state_sha256": hashlib.sha256(state_canonical.encode("utf-8")).hexdigest(),
                  "previous_hash": previous_hash}
        canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        record["hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def initialize(self, start: str, end: str, initial_cash: Decimal):
        if self.state is not None:
            raise ValueError("实验账本已经存在")
        self.state = {
            "version": 2, "status": "ACTIVE", "start_date": start, "end_date": end,
            "initial_cash": str(qmoney(initial_cash)), "cash_available": str(qmoney(initial_cash)),
            "cash_frozen": "0.00", "positions": {}, "orders": [], "transactions": [], "valuations": [],
            "decisions": [],
            "rules": {"execution": "orders after 15:00 use the next trading day's NAV; confirmation is normally T+1",
                      "lookahead": "orders cannot be edited or cancelled after registration"},
        }
        self.save("LEDGER_INITIALIZED", {"start": start, "end": end, "initial_cash": str(qmoney(initial_cash))})

    def record_decision(self, decision_id: str, decision_date: str, action: str,
                        market_observation: str, reason: str, confidence: int,
                        evidence: list[str] | None = None, counter_evidence: str = "",
                        invalidation_conditions: str = "", data_as_of: str | None = None,
                        user_confirmation: str = "") -> dict:
        decisions = self.state.setdefault("decisions", [])
        if any(item.get("decision_id") == decision_id for item in decisions):
            raise ValueError("决策编号已经存在，历史决策禁止覆盖")
        if not market_observation.strip() or not reason.strip():
            raise ValueError("市场观察和决策理由不能为空")
        if not user_confirmation.strip():
            raise ValueError("用户尚未明确确认，禁止写入决策账本")
        if not 0 <= confidence <= 100:
            raise ValueError("置信度必须在 0 到 100 之间")
        record = {
            "decision_id": decision_id, "decision_date": decision_date,
            "data_as_of": data_as_of or decision_date, "action": action.upper(),
            "market_observation": market_observation.strip(), "reason": reason.strip(),
            "counter_evidence": counter_evidence.strip(),
            "invalidation_conditions": invalidation_conditions.strip(),
            "confidence": confidence, "evidence": evidence or [],
            "user_confirmation": user_confirmation.strip(),
            "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"), "immutable": True,
        }
        decisions.append(record)
        self.save("DECISION_RECORDED", record)
        return record

    def annotate_decision(self, annotation_id: str, decision_id: str, status: str,
                          reason: str, user_confirmation: str) -> dict:
        decisions = self.state.get("decisions", [])
        decision = next((item for item in decisions if item.get("decision_id") == decision_id), None)
        if decision is None:
            raise ValueError("要标记的 AI 决策记录不存在")
        annotations = self.state.setdefault("decision_annotations", [])
        if any(item.get("annotation_id") == annotation_id for item in annotations):
            raise ValueError("决策标记编号已经存在，历史标记禁止覆盖")
        normalized_status = status.upper()
        if normalized_status not in {"ACTIVE", "VOIDED", "VOIDED_DUPLICATE", "VOIDED_SUPERSEDED", "SUPERSEDED"}:
            raise ValueError("不支持的决策标记状态")
        if not reason.strip() or not user_confirmation.strip():
            raise ValueError("作废理由和用户确认信息不能为空")
        linked_orders = [order for order in self.state.get("orders", [])
                         if order.get("decision_id") == decision_id]
        if normalized_status in {"VOIDED_DUPLICATE", "VOIDED_SUPERSEDED", "SUPERSEDED"} and linked_orders:
            raise ValueError("已关联订单的决策不能标记为重复或被替代")
        if normalized_status == "VOIDED" and any(order.get("status") != "CANCELLED" for order in linked_orders):
            raise ValueError("关联订单必须先撤销；已成交订单应通过新的反向决策处理")
        record = {
            "annotation_id": annotation_id,
            "decision_id": decision_id,
            "status": normalized_status,
            "reason": reason.strip(),
            "user_confirmation": user_confirmation.strip(),
            "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "immutable": True,
        }
        annotations.append(record)
        self.save("DECISION_ANNOTATED", record)
        return record

    def decision_status(self, decision_id: str) -> str:
        status = "ACTIVE"
        for annotation in self.state.get("decision_annotations", []):
            if annotation.get("decision_id") == decision_id:
                status = annotation.get("status", status)
        return status

    def _require_decision(self, decision_id: str | None, expected_actions: set[str]) -> None:
        if not decision_id:
            return
        decision = next((item for item in self.state.get("decisions", [])
                         if item.get("decision_id") == decision_id), None)
        if decision is None:
            raise ValueError("关联的 AI 决策记录不存在")
        if self.decision_status(decision_id) != "ACTIVE":
            raise ValueError("关联的 AI 决策已被标记为无效")
        if decision.get("action") not in expected_actions:
            raise ValueError("决策动作与账本操作不一致")

    def register_buy(self, order_id: str, decision_date: str, nav_date: str, confirmation_date: str,
                     code: str, name: str,
                     amount: Decimal, subscription_fee_rate: Decimal, evidence: list[str], thesis: str,
                     decision_id: str | None = None):
        self._require_decision(decision_id, {"BUY", "ADD", "REBALANCE"})
        if any(x["order_id"] == order_id for x in self.state["orders"]):
            raise ValueError("订单编号已经存在")
        amount = qmoney(amount)
        available = qmoney(self.state["cash_available"])
        if amount > available:
            raise ValueError("可用现金不足")
        self.state["cash_available"] = str(qmoney(available - amount))
        self.state["cash_frozen"] = str(qmoney(Decimal(self.state["cash_frozen"]) + amount))
        self.state["orders"].append({
            "order_id": order_id, "side": "BUY", "status": "PENDING_NAV", "decision_date": decision_date,
            "nav_date": nav_date, "confirmation_date": confirmation_date,
            "fund_code": code, "fund_name": name, "gross_amount": str(amount),
            "subscription_fee_rate": str(subscription_fee_rate), "evidence": evidence, "thesis": thesis,
            "decision_id": decision_id, "immutable": True,
        })
        self.save("ORDER_REGISTERED", {"order_id": order_id, "side": "BUY", "nav_date": nav_date,
                                       "confirmation_date": confirmation_date, "fund_code": code,
                                       "amount": str(amount)})

    @staticmethod
    def _nav_date(order: dict) -> str:
        return order.get("nav_date", order.get("execution_date"))

    @classmethod
    def _confirmation_date(cls, order: dict) -> str:
        return order.get("confirmation_date", cls._nav_date(order))

    def correct_order_schedule(self, order_id: str, nav_date: str, confirmation_date: str,
                               reason: str, evidence: list[str] | None = None) -> dict:
        order = next((item for item in self.state["orders"] if item["order_id"] == order_id), None)
        if order is None:
            raise ValueError("订单不存在")
        if order["status"] != "PENDING_NAV":
            raise ValueError("只能更正尚未结算的订单日期")
        previous = {
            "decision_date": order["decision_date"],
            "execution_date": order.get("execution_date"),
            "nav_date": order.get("nav_date"),
            "confirmation_date": order.get("confirmation_date"),
        }
        order.pop("execution_date", None)
        order["nav_date"] = nav_date
        order["confirmation_date"] = confirmation_date
        self.state["version"] = 2
        self.state.setdefault("rules", {})["execution"] = (
            "orders after 15:00 use the next trading day's NAV; confirmation is normally T+1"
        )
        corrected = {
            "decision_date": order["decision_date"],
            "nav_date": nav_date,
            "confirmation_date": confirmation_date,
        }
        payload = {
            "order_id": order_id,
            "reason": reason,
            "previous_schedule": previous,
            "corrected_schedule": corrected,
            "evidence": evidence or [],
        }
        self.save("ORDER_SCHEDULE_CORRECTED", payload)
        return payload

    def settle_due_buys(self, as_of: str) -> list[dict]:
        settled = []
        for order in self.state["orders"]:
            if order["side"] != "BUY" or order["status"] != "PENDING_NAV":
                continue
            nav_date = self._nav_date(order)
            if nav_date > as_of:
                continue
            frame = ak.fund_open_fund_info_em(order["fund_code"], "单位净值走势")
            frame["date"] = pd.to_datetime(frame["净值日期"]).dt.date.astype(str)
            match = frame[frame["date"] == nav_date]
            if match.empty:
                continue
            nav = Decimal(str(match.iloc[0]["单位净值"]))
            gross = Decimal(order["gross_amount"])
            fee = qmoney(gross * Decimal(order["subscription_fee_rate"]))
            shares = qshares((gross - fee) / nav)
            position = self.state["positions"].setdefault(order["fund_code"], {"name": order["fund_name"], "shares_frozen": "0.0000", "lots": []})
            position["lots"].append({"order_id": order["order_id"],
                                     "nav_date": nav_date,
                                     "confirmation_date": self._confirmation_date(order),
                                     "nav": str(nav), "shares": str(shares), "shares_remaining": str(shares),
                                     "cost": str(gross), "cost_remaining": str(gross), "fee": str(fee)})
            order.update({"status": "FILLED", "nav": str(nav), "fee": str(fee), "shares": str(shares)})
            self.state["cash_frozen"] = str(qmoney(Decimal(self.state["cash_frozen"]) - gross))
            transaction = {"type": "BUY_FILLED", "date": nav_date, "nav_date": nav_date,
                           "confirmation_date": self._confirmation_date(order), "order_id": order["order_id"],
                           "fund_code": order["fund_code"], "nav": str(nav),
                           "shares": str(shares), "fee": str(fee)}
            self.state["transactions"].append(transaction)
            settled.append(transaction)
        if settled:
            self.save("ORDERS_SETTLED", {"transactions": settled})
        else:
            self.save()
        return settled

    def register_sell(self, order_id: str, decision_date: str, nav_date: str, confirmation_date: str, code: str,
                      shares: Decimal, fee_schedule: list[dict], evidence: list[str], thesis: str,
                      decision_id: str | None = None):
        self._require_decision(decision_id, {"REDUCE", "SELL", "REBALANCE"})
        if any(x["order_id"] == order_id for x in self.state["orders"]):
            raise ValueError("订单编号已经存在")
        if code not in self.state["positions"]:
            raise ValueError("没有可卖出的基金持仓")
        position = self.state["positions"][code]
        shares = qshares(shares)
        total = sum(Decimal(x.get("shares_remaining", x["shares"])) for x in position["lots"])
        frozen = Decimal(position.get("shares_frozen", "0"))
        if shares <= 0 or shares > total - frozen:
            raise ValueError("可用份额不足")
        position["shares_frozen"] = str(qshares(frozen + shares))
        self.state["orders"].append({
            "order_id": order_id, "side": "SELL", "status": "PENDING_NAV", "decision_date": decision_date,
            "nav_date": nav_date, "confirmation_date": confirmation_date,
            "fund_code": code, "fund_name": position["name"],
            "shares": str(shares), "fee_schedule": fee_schedule, "evidence": evidence, "thesis": thesis,
            "decision_id": decision_id, "immutable": True,
        })
        self.save("ORDER_REGISTERED", {"order_id": order_id, "side": "SELL", "nav_date": nav_date,
                                       "confirmation_date": confirmation_date, "fund_code": code,
                                       "shares": str(shares)})

    def cancel_order(self, order_id: str, reason: str, user_confirmation: str) -> dict:
        """Cancel an unsettled order while preserving the original order and audit trail."""
        if not reason.strip() or not user_confirmation.strip():
            raise ValueError("cancellation reason and user confirmation are required")
        order = next((item for item in self.state.get("orders", []) if item.get("order_id") == order_id), None)
        if order is None:
            raise ValueError("order does not exist")
        if order.get("status") != "PENDING_NAV":
            raise ValueError("only unsettled PENDING_NAV orders can be cancelled")
        if order.get("side") == "BUY":
            amount = Decimal(order["gross_amount"])
            self.state["cash_frozen"] = str(qmoney(Decimal(self.state["cash_frozen"]) - amount))
            self.state["cash_available"] = str(qmoney(Decimal(self.state["cash_available"]) + amount))
        elif order.get("side") == "SELL":
            position = self.state.get("positions", {}).get(order["fund_code"])
            if position is None:
                raise ValueError("position for pending sell does not exist")
            position["shares_frozen"] = str(qshares(Decimal(position.get("shares_frozen", "0")) - Decimal(order["shares"])))
        else:
            raise ValueError("unsupported order side")
        order["status"] = "CANCELLED"
        order["cancelled_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        payload = {"order_id": order_id, "side": order["side"], "reason": reason.strip(),
                   "user_confirmation": user_confirmation.strip()}
        self.save("ORDER_CANCELLED", payload)
        return {**order, "cancellation": payload}

    @staticmethod
    def _redemption_rate(held_days: int, schedule: list[dict]) -> Decimal:
        for item in schedule:
            upper = item.get("max_days_exclusive")
            if held_days >= item["min_days"] and (upper is None or held_days < upper):
                return Decimal(str(item["rate"]))
        raise ValueError(f"赎回费表没有覆盖持有天数 {held_days}")

    def settle_sell(self, order: dict, nav: Decimal) -> dict:
        position = self.state["positions"][order["fund_code"]]
        remaining = Decimal(order["shares"])
        gross = Decimal("0"); total_fee = Decimal("0"); cost_basis = Decimal("0"); lot_details = []
        for lot in position["lots"]:
            available = Decimal(lot.get("shares_remaining", lot["shares"]))
            if available <= 0 or remaining <= 0:
                continue
            used = min(available, remaining)
            original_remaining = available
            held_days = (
                date.fromisoformat(self._confirmation_date(order)) -
                date.fromisoformat(lot["confirmation_date"])
            ).days
            rate = self._redemption_rate(held_days, order["fee_schedule"])
            lot_gross = qmoney(used * nav)
            fee = qmoney(lot_gross * rate)
            lot_cost = qmoney(Decimal(lot.get("cost_remaining", lot["cost"])) * used / original_remaining)
            lot["shares_remaining"] = str(qshares(available - used))
            lot["cost_remaining"] = str(qmoney(Decimal(lot.get("cost_remaining", lot["cost"])) - lot_cost))
            gross += lot_gross; total_fee += fee; cost_basis += lot_cost; remaining -= used
            lot_details.append({"buy_order_id": lot["order_id"], "shares": str(qshares(used)),
                                "held_days": held_days, "rate": str(rate), "fee": str(fee)})
        if remaining > 0:
            raise ValueError("FIFO结算时份额不足")
        net = qmoney(gross - total_fee)
        self.state["cash_available"] = str(qmoney(Decimal(self.state["cash_available"]) + net))
        position["shares_frozen"] = str(qshares(Decimal(position.get("shares_frozen", "0")) - Decimal(order["shares"])))
        order.update({"status": "FILLED", "nav": str(nav), "gross_proceeds": str(qmoney(gross)),
                      "fee": str(qmoney(total_fee)), "net_proceeds": str(net), "cost_basis": str(qmoney(cost_basis)),
                      "realized_pnl": str(qmoney(net - cost_basis)), "lots": lot_details})
        nav_date = self._nav_date(order)
        transaction = {"type": "SELL_FILLED", "date": nav_date, "nav_date": nav_date,
                       "confirmation_date": self._confirmation_date(order), "order_id": order["order_id"],
                       "fund_code": order["fund_code"], "nav": str(nav), "shares": order["shares"],
                       "fee": str(qmoney(total_fee)), "net_proceeds": str(net),
                       "realized_pnl": str(qmoney(net - cost_basis)), "lots": lot_details}
        self.state["transactions"].append(transaction)
        return transaction

    def settle_due_sells(self, as_of: str) -> list[dict]:
        settled = []
        for order in self.state["orders"]:
            if order["side"] != "SELL" or order["status"] != "PENDING_NAV":
                continue
            nav_date = self._nav_date(order)
            if nav_date > as_of:
                continue
            frame = ak.fund_open_fund_info_em(order["fund_code"], "单位净值走势")
            frame["date"] = pd.to_datetime(frame["净值日期"]).dt.date.astype(str)
            match = frame[frame["date"] == nav_date]
            if not match.empty:
                settled.append(self.settle_sell(order, Decimal(str(match.iloc[0]["单位净值"]))))
        if settled:
            self.save("ORDERS_SETTLED", {"transactions": settled})
        return settled

    def record_valuation(self, valuation_date: str, navs: dict[str, Decimal]) -> dict:
        market_value = Decimal("0")
        positions = {}
        for code, position in self.state["positions"].items():
            shares = sum(Decimal(x.get("shares_remaining", x["shares"])) for x in position["lots"])
            if shares <= 0:
                continue
            if code not in navs:
                raise ValueError(f"缺少 {code} 的正式净值")
            value = qmoney(shares * Decimal(navs[code]))
            positions[code] = {"shares": str(qshares(shares)), "nav": str(navs[code]), "market_value": str(value)}
            market_value += value
        cash = Decimal(self.state["cash_available"]) + Decimal(self.state["cash_frozen"])
        total = qmoney(cash + market_value)
        history = self.state.setdefault("valuations", [])
        prior_totals = [Decimal(x["total_assets"]) for x in history]
        peak = max(prior_totals + [total])
        initial = Decimal(self.state["initial_cash"])
        entry = {"date": valuation_date, "cash": str(qmoney(cash)), "market_value": str(qmoney(market_value)),
                 "total_assets": str(total), "return_percent": str(qmoney((total / initial - 1) * 100)),
                 "drawdown_percent": str(qmoney((total / peak - 1) * 100)), "positions": positions}
        if history and history[-1]["date"] == valuation_date:
            raise ValueError("同一日期的估值已经存在，禁止覆盖")
        history.append(entry)
        self.save("VALUATION_RECORDED", entry)
        return entry

    def record_official_valuation(self, valuation_date: str) -> dict:
        navs = {}
        for code, position in self.state["positions"].items():
            shares = sum(Decimal(x.get("shares_remaining", x["shares"])) for x in position["lots"])
            if shares <= 0:
                continue
            frame = ak.fund_open_fund_info_em(code, "单位净值走势")
            frame["date"] = pd.to_datetime(frame["净值日期"]).dt.date.astype(str)
            match = frame[frame["date"] == valuation_date]
            if match.empty:
                raise ValueError(f"{code} 尚未发布 {valuation_date} 的正式净值")
            navs[code] = Decimal(str(match.iloc[0]["单位净值"]))
        return self.record_valuation(valuation_date, navs)

    def checkpoint(self, reason: str):
        canonical = json.dumps(self.state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self._audit("STATE_CHECKPOINT", {"reason": reason, "state_sha256": digest})
        return digest

    def verify_audit(self) -> dict:
        audit_path = self.path.with_name(f"{self.path.stem}.audit.jsonl")
        if not audit_path.exists():
            return {"valid": False, "records": 0, "reason": "审计文件不存在"}
        previous = "GENESIS"
        records = [json.loads(x) for x in audit_path.read_text(encoding="utf-8").splitlines() if x.strip()]
        for index, record in enumerate(records, start=1):
            supplied = record.pop("hash")
            canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            calculated = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if supplied != calculated or record["previous_hash"] != previous:
                return {"valid": False, "records": len(records), "reason": f"第{index}条记录哈希或前序指针错误"}
            previous = supplied
            record["hash"] = supplied
        current = json.dumps(self.state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        current_hash = hashlib.sha256(current.encode("utf-8")).hexdigest()
        last_state_hash = records[-1].get("state_sha256") if records else None
        if last_state_hash is None:
            return {"valid": False, "records": len(records), "reason": "最后一条记录未绑定账本状态"}
        if current_hash != last_state_hash:
            return {"valid": False, "records": len(records), "reason": "当前账本与最后审计状态不一致"}
        return {"valid": True, "records": len(records), "state_sha256": current_hash, "tail_hash": previous}

    def daily_close(self, as_of: str) -> dict:
        if as_of > date.today().isoformat():
            raise ValueError("禁止处理未来日期")
        if as_of < self.state["start_date"] or as_of > self.state["end_date"]:
            raise ValueError("日期不在实验周期内")
        buy_transactions = self.settle_due_buys(as_of)
        sell_transactions = self.settle_due_sells(as_of)
        existing = [x for x in self.state.get("valuations", []) if x["date"] == as_of]
        valuation = existing[-1] if existing else self.record_official_valuation(as_of)
        return {"date": as_of, "settled": buy_transactions + sell_transactions, "valuation": valuation,
                "summary": self.summary()}

    def write_daily_report(self, result: dict, report_dir: Path) -> Path:
        report_dir.mkdir(parents=True, exist_ok=True)
        target = report_dir / f"{result['date']}.md"
        if target.exists():
            return target
        valuation = result["valuation"]
        lines = [
            f"# 模拟经营日报 — {result['date']}", "", "## 资产", "",
            f"- 总资产：{valuation['total_assets']} 元", f"- 现金及冻结现金：{valuation['cash']} 元",
            f"- 基金市值：{valuation['market_value']} 元", f"- 累计收益：{valuation['return_percent']}%",
            f"- 当前回撤：{valuation['drawdown_percent']}%", "", "## 当日成交", "",
        ]
        if result["settled"]:
            for tx in result["settled"]:
                lines.append(f"- {tx['type']}｜{tx['order_id']}｜{tx['fund_code']}｜净值 {tx['nav']}｜费用 {tx['fee']} 元")
        else:
            lines.append("- 无")
        benchmark = result.get("benchmark")
        if benchmark:
            returns = benchmark["returns_percent"]
            excess = benchmark.get("excess_percent", {})
            lines += ["", "## 同期基准", "",
                      f"- 50%沪深300 + 50%现金：{returns['cash_50_csi300_50']}%",
                      f"- 保险行业指数：{returns['industry']}%",
                      f"- 首日全额买入候选基金：{returns['fund_buy_hold']}%",
                      f"- 组合相对平衡基准超额：{excess.get('vs_cash_csi300', '—')}%",
                      f"- 组合相对行业超额：{excess.get('vs_industry', '—')}%",
                      f"- 组合相对基金买入持有超额：{excess.get('vs_fund_buy_hold', '—')}%"]
        lines += ["", "## 当前状态", "", f"- 可用现金：{result['summary']['cash_available']} 元",
                  f"- 冻结现金：{result['summary']['cash_frozen']} 元",
                  f"- 待执行订单：{result['summary']['pending_orders']} 笔",
                  f"- 持仓基金：{result['summary']['position_count']} 只", "",
                  "> 本报告仅记录当日已经公开的正式净值，不使用盘中估值。"]
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return target

    def summary(self) -> dict:
        return {"cash_available": self.state["cash_available"], "cash_frozen": self.state["cash_frozen"],
                "pending_orders": sum(x["status"] == "PENDING_NAV" for x in self.state["orders"]),
                "position_count": sum(any(Decimal(l.get("shares_remaining", l["shares"])) > 0 for l in p["lots"])
                                      for p in self.state["positions"].values()), "status": self.state["status"]}


def fetch_trading_dates(start: str, end: str) -> list[str]:
    frame = ak.tool_trade_date_hist_sina()
    dates = pd.to_datetime(frame["trade_date"]).dt.date.astype(str)
    return [x for x in dates if start <= x <= end]
