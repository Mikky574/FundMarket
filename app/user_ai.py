from __future__ import annotations

import json
import re
import subprocess
import shutil
import queue
import threading
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException

from app.config import settings
from app.fund_service import get_fund_overview
from app.user_trading import portfolio

DISPLAY_NAMES = {"025491": "平安中证卫星产业指数C"}


def _path(user_id: int) -> Path:
    root = Path(settings.user_ai_root).resolve() / str(user_id)
    root.mkdir(parents=True, exist_ok=True)
    return root / "state.json"


def delete(user_id: int) -> None:
    base = Path(settings.user_ai_root).resolve()
    root = (base / str(user_id)).resolve()
    if base not in root.parents:
        raise RuntimeError("invalid user AI path")
    if root.exists():
        shutil.rmtree(root)


def clear_context(user_id: int) -> None:
    path = _path(user_id)
    if not path.exists():
        raise HTTPException(409, "请先创建独立 AI 组合")
    state = json.loads(path.read_text(encoding="utf-8"))
    state["chat_history"] = []
    state["codex_session"] = None
    _write(path, state)


def _write(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _reconcile_cancelled_conversions(state: dict) -> None:
    """Undo a legacy cancelled conversion that incorrectly changed the AI position."""
    reconciled = set(state.setdefault("reconciled_cancelled_operations", []))
    for operation in list(state.get("operations", [])):
        if operation.get("status") != "cancelled_not_confirmed":
            continue
        key = operation.get("timestamp")
        if not key or key in reconciled:
            continue
        for transaction in operation.get("transactions", []):
            if not str(transaction.get("action", "")).startswith("simulated_redeem"):
                continue
            position = next((item for item in state.get("positions", []) if item.get("fund_code") == transaction.get("fund_code")), None)
            if not position:
                continue
            position["shares"] = str(Decimal(str(position["shares"])) + Decimal(str(transaction["shares"])))
            position["cost_basis"] = str(Decimal(str(position["cost_basis"])) + Decimal(str(transaction["cost_basis_redeemed"])))
        reconciled.add(key)
        state.setdefault("operations", []).append({"timestamp": datetime.now().astimezone().isoformat(timespec="seconds"), "type": "ledger_reconciliation", "status": "completed", "reason": "已恢复一笔标记为未确认取消的历史模拟转换对原基金份额和成本的错误影响；该恢复不改变待确认的沪深300转换订单。"})
    state["reconciled_cancelled_operations"] = sorted(reconciled)


def _snapshot(user_id: int) -> dict:
    source = portfolio(user_id)
    return {
        "cash": source["account"]["cash_available"],
        "positions": [{key: item[key] for key in ("fund_code", "fund_name", "shares", "cost_basis")}
                      for item in source["positions"]],
        "source_total_assets": source["summary"]["total_assets"],
    }


def create_or_sync(user_id: int, confirmed: bool = False) -> dict:
    path = _path(user_id)
    snapshot = _snapshot(user_id)
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if path.exists() and not confirmed:
        return {"requires_confirmation": True, "current_ai_positions": existing.get("positions", []),
                "user_positions": snapshot["positions"]}
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    state = {"version": 1, "user_id": user_id, "created_at": now, "synced_at": now,
             "cash": snapshot["cash"], "positions": snapshot["positions"], "operations": [],
             "pending_orders": [], "initial_total_assets": snapshot["source_total_assets"],
             "initial_user_total_assets": snapshot["source_total_assets"], "daily": [],
             "chat_history": (existing or {}).get("chat_history", []), "codex_session": (existing or {}).get("codex_session")}
    _write(path, state)
    return {"requires_confirmation": False, "state": view(user_id)}


def view(user_id: int, force_refresh: bool = False) -> dict:
    path = _path(user_id)
    if not path.exists():
        return {"initialized": False}
    state = json.loads(path.read_text(encoding="utf-8"))
    _reconcile_cancelled_conversions(state)
    value = Decimal(state["cash"])
    positions = []
    for item in state["positions"]:
        # A subscription without confirmed NAV is an order, not a holding.
        if item.get("shares") is None or str(item.get("status", "")).startswith("subscription_pending"):
            continue
        try:
            fund = get_fund_overview(item["fund_code"], force_refresh=force_refresh); nav = Decimal(str(fund["latest"]["nav"]))
            market = (Decimal(item["shares"]) * nav).quantize(Decimal("0.01")); pnl = market - Decimal(item["cost_basis"])
            positions.append({**item, "fund_name": fund.get("name") or item["fund_name"], "nav": str(nav), "market_value": str(market), "pnl": str(pnl), "as_of": fund["latest"]["date"]}); value += market
        except Exception:
            positions.append({**item, "fund_name": DISPLAY_NAMES.get(item["fund_code"], item["fund_name"]), "nav": None, "market_value": "0.00", "pnl": "0.00", "as_of": None})
    # Pending subscriptions remain assets at their booked amount until a NAV can create shares.
    # An order that only waits for confirmation has not left cash yet.  Count it
    # as an asset only when its cash/source position was already reserved.
    pending_value = sum((Decimal(str(order.get("amount", 0))) for order in state.get("pending_orders", [])
                         if str(order.get("status", "")).lower().startswith("pending") and order.get("cash_reserved") is True), Decimal("0"))
    total = (value + pending_value).quantize(Decimal("0.01")); user = portfolio(user_id)
    # One-time repair for legacy records whose rounded source shares did not equal
    # their booked conversion amount.  The independent AI began as a copy of the
    # user account, so a small initial mismatch is ledger residue, not investment return.
    if state.get("reconciled_cancelled_operations") and not state.get("rounding_residue_reconciled"):
        reference = Decimal(str(state.get("initial_user_total_assets", user["summary"]["total_assets"])))
        residue = total - reference
        if Decimal("-5.00") <= residue <= Decimal("5.00") and residue:
            state["cash"] = str((Decimal(str(state["cash"])) - residue).quantize(Decimal("0.01")))
            value -= residue
            total = (value + pending_value).quantize(Decimal("0.01"))
            state["initial_total_assets"] = str(total)
            state.setdefault("operations", []).append({"timestamp": datetime.now().astimezone().isoformat(timespec="seconds"), "type": "rounding_residue_reconciliation", "status": "completed", "reason": f"已冲回历史模拟操作产生的 ¥{residue:.2f} 账务尾差；该金额不是手续费、收益或亏损。"})
        state["rounding_residue_reconciled"] = True
    if state.get("rounding_residue_reconciled") and not state.get("comparison_baseline_after_rounding"):
        state["initial_total_assets"] = str(total)
        state["comparison_baseline_after_rounding"] = True
    # Legacy records used differently rounded source values.  After their one-time
    # reconciliation, start the comparison from the reconciled balance rather than
    # displaying that rounding residue as investment profit.
    if state.get("reconciled_cancelled_operations") and not state.get("comparison_baseline_reconciled"):
        state["initial_total_assets"] = str(total)
        state["initial_user_total_assets"] = user["summary"]["total_assets"]
        state["comparison_baseline_reconciled"] = True
    first_daily = (state.get("daily") or [{}])[0]
    initial_ai = Decimal(str(state.setdefault("initial_total_assets", first_daily.get("user_total_assets", total))))
    initial_user = Decimal(str(state.setdefault("initial_user_total_assets", first_daily.get("user_total_assets", user["summary"]["total_assets"]))))
    today = datetime.now().astimezone().date().isoformat()
    daily = state.setdefault("daily", [])
    snapshot = {"date": today, "ai_total_assets": str(total), "user_total_assets": user["summary"]["total_assets"],
                "ai_pnl": str(total - initial_ai), "user_pnl": str(Decimal(user["summary"]["total_assets"]) - initial_user)}
    if daily and daily[-1].get("date") == today:
        daily[-1] = snapshot
    else:
        daily.append(snapshot)
    state["daily"] = daily[-366:]
    _write(path, state)
    return {"initialized": True, "refreshed_at": datetime.now().astimezone().isoformat(timespec="seconds"), "synced_at": state["synced_at"], "cash": state["cash"], "positions": positions,
            "chat_history": state.get("chat_history", [])[-30:], "daily": state["daily"],
            "pending_orders": state.get("pending_orders", [])[-30:],
            "operations": state.get("operations", [])[-30:], "summary": {"total_assets": str(total),
            "pnl": str(total - initial_ai), "daily_pnl": None},
            "comparison": {"user_total_assets": user["summary"]["total_assets"], "user_pnl": str(Decimal(user["summary"]["total_assets"]) - initial_user),
                           "ai_total_assets": str(total), "ai_relative_to_user": str(total - Decimal(user["summary"]["total_assets"]))}}


def ask(user_id: int, prompt: str) -> dict:
    path = _path(user_id)
    if not path.exists():
        raise HTTPException(409, "请先创建独立 AI 组合")
    state = json.loads(path.read_text(encoding="utf-8"))
    history = state.get("chat_history", [])[-12:]
    context = "\n".join(f"{item['role']}：{item['content']}" for item in history)
    live = view(user_id)
    market_context = json.dumps({"ai_portfolio": {"cash": live["cash"], "positions": live["positions"], "summary": live["summary"]},
                                 "user_comparison": live["comparison"]}, ensure_ascii=False)
    instruction = "你是用户独立基金 AI。只能读写当前目录中的独立 AI 账本；不得访问或操作公共AI账本、用户交易数据库或项目其他目录。你可自动调整自己的独立持仓并追加操作记录。以下实时市场上下文由服务端刚刚通过基金行情接口查询得到，必须优先据此分析；不要声称缺少净值或市值，除非对应字段明确为空。回复必须自然、具体且可继续追问。严禁显示或提及任何本地文件路径、文件名、工具调用、命令、内部账本实现或权限设置。\n\n投资判断原则（必须遵守）：\n1. 保持独立、稳定的判断：用户的焦虑、亏损感受或单次追问不是交易信号。先评估数据、仓位、集中度、现金和反方证据，再决定执行、继续观察或拒绝交易。\n2. 不为“拯救亏损”而追涨、摊薄、频繁换仓或立即反向操作。没有新的可验证信息时，延续上一有效计划，并明确说明不行动的理由与下一次复核条件。\n3. 每次交易都要有明确目标仓位、金额上限、失效条件和复核时间；连续两次相反交易前必须说明出现了什么新的数据证据。\n4. 回复先给结论，再给依据、风险和后续条件；不要把选择完全推回用户，也不要承诺收益。\n\n账本与回复契约（必须遵守）：\n1. 只有在你已实际更新现金、持仓和 operations 中 status=completed 的记录后，才可说“已执行”或“已模拟执行”。\n2. 若因确认净值、份额或其他原因暂不能入账，必须把订单加入 pending_orders（至少含 fund_code、fund_name、amount、side、status、created_at、reason、cash_reserved）。cash_reserved 仅在现金或来源持仓已经实际扣减/冻结时为 true；普通待确认订单必须为 false，且绝不能计入资产或盈亏。回复只能说“已提交待确认”，不得说已执行、已转入或已转出。\n3. status=cancelled_not_confirmed 或其他取消状态表示没有成交；必须明确说“未执行/已取消”，不得把它描述为持仓变化。\n4. 不得删除或覆盖既有操作历史。\n\n实时市场上下文：\n" + market_context + "\n历史对话：\n" + context + "\n用户问题：" + prompt
    try:
        completed = subprocess.run(
            [settings.codex_command, "exec", "--model", settings.user_ai_codex_model,
             "--config", f'model_reasoning_effort="{settings.user_ai_codex_reasoning_effort}"',
             "--sandbox", "workspace-write", "--skip-git-repo-check", "-C", str(path.parent), instruction],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=settings.user_ai_codex_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, "独立 Codex 分析超时") from exc
    except OSError as exc:
        raise HTTPException(502, f"无法启动独立 Codex：{exc}") from exc
    if completed.returncode:
        raise HTTPException(502, completed.stderr[-1000:] or "独立 Codex 调用失败")
    answer = completed.stdout[-8000:].strip()
    answer = re.sub(r"\[[^\]]+\]\([^)]*[A-Za-z]:[\\/][^)]*\)", "", answer)
    answer = re.sub(r"[A-Za-z]:\\[^\s`）)]+", "", answer)
    state = json.loads(path.read_text(encoding="utf-8"))
    state.setdefault("chat_history", []).extend([
        {"role": "用户", "content": prompt, "at": datetime.now().astimezone().isoformat(timespec="seconds")},
        {"role": "AI", "content": answer, "at": datetime.now().astimezone().isoformat(timespec="seconds")},
    ])
    state["chat_history"] = state["chat_history"][-60:]
    _write(path, state)
    return {"answer": answer, "portfolio": view(user_id)}


def ask_stream(user_id: int, prompt: str):
    """Yield progress immediately while the isolated Codex turn runs in a worker."""
    results: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)

    def run() -> None:
        try:
            results.put(("result", ask(user_id, prompt)))
        except HTTPException as exc:
            results.put(("error", {"detail": exc.detail, "status": exc.status_code}))
        except Exception:
            results.put(("error", {"detail": "独立 AI 分析异常", "status": 500}))

    threading.Thread(target=run, daemon=True).start()
    stages = [(0, "已接收问题，正在读取独立 AI 账本与最新行情…"),
              (2, "正在评估仓位、现金和待确认订单…"),
              (6, "正在形成独立判断并检查反方风险…"),
              (12, "正在整理结论与可执行操作…")]
    started = time.monotonic(); sent = 0
    yield json.dumps({"type": "stage", "message": stages[0][1]}, ensure_ascii=False) + "\n"
    while True:
        try:
            kind, payload = results.get(timeout=0.5)
            yield json.dumps({"type": kind, **(payload if isinstance(payload, dict) else {})}, ensure_ascii=False) + "\n"
            return
        except queue.Empty:
            elapsed = time.monotonic() - started
            index = max(i for i, stage in enumerate(stages) if elapsed >= stage[0])
            if index != sent:
                sent = index
                yield json.dumps({"type": "stage", "message": stages[index][1]}, ensure_ascii=False) + "\n"
