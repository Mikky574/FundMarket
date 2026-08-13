"""Public-AI decision drafts for the QQ/Codex bridge only.

This module is deliberately not mounted by FastAPI.  It reads dated public
evidence and the public paper ledger, invokes Codex in a disposable read-only
directory, and returns a draft.  It never calls paper_cli and never mutates a
ledger or writes a decision artifact.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.market_intelligence import latest

ALLOWED_ACTIONS = {"WATCH", "BUY", "ADD", "REDUCE", "SELL", "REBALANCE"}


def _as_datetime(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value or "")
    except ValueError:
        return None


def public_evidence_packet() -> dict:
    """Return fresh public evidence or fail closed before any model is called."""
    intelligence = latest()
    if not intelligence.get("available"):
        raise RuntimeError("市场情报尚未生成，不能形成公共 AI 决策草案")
    generated_at = _as_datetime(intelligence.get("generated_at"))
    if not generated_at:
        raise RuntimeError("市场情报缺少生成时间，不能形成公共 AI 决策草案")
    age_minutes = (datetime.now().astimezone() - generated_at.astimezone()).total_seconds() / 60
    if age_minutes > settings.market_intelligence_max_age_minutes:
        raise RuntimeError(f"市场情报已过期（{age_minutes:.0f} 分钟），请先刷新研究证据")
    state_path = Path("paper/state.json")
    if not state_path.exists():
        raise RuntimeError("公共 AI 账本不存在")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    # Never send user-account information.  The source packet contains public
    # positions, research candidates, quantitative evidence, and recent news.
    return {
        "evidence_generated_at": intelligence["generated_at"],
        "evidence_age_minutes": round(age_minutes, 1),
        "data_through": intelligence.get("data_through"),
        "market_intelligence": {key: intelligence.get(key) for key in (
            "benchmark", "market_regime", "top_industries", "portfolio_watchlist",
            "public_research_watchlist", "opportunity_candidates", "news", "interpretation", "limitations")},
        "public_paper_portfolio": {key: state.get(key) for key in (
            "status", "start_date", "end_date", "cash_available", "cash_frozen", "positions", "orders")},
    }


def _instruction(packet: dict, question: str) -> str:
    return """你是公共 AI 模拟基金的顶层研究与决策审查者。只根据下方证据包形成“未执行的决策草案”。
你不是交易执行器：不得写文件、不得调用命令、不得声称已交易、不得要求或暗示绕过用户确认。
DeepSeek 摘要只是一份二级研究材料；必须优先检查数据时间、原始量化字段、来源局限和反方证据。数据矛盾、过期、缺失或单一媒体线索时默认 WATCH。
不得使用用户个人账户、客户关注池或任何未提供的信息。对于 BUY/ADD/REDUCE/SELL/REBALANCE，必须写明目标/上限、证据、反证、失效条件和复核时间。
返回严格 JSON，且只返回 JSON：
{"action":"WATCH|BUY|ADD|REDUCE|SELL|REBALANCE","headline":"","market_observation":"","reason":"","counter_evidence":"","invalidation_conditions":"","confidence":0,"position_guardrail":"","review_at":"","data_as_of":"","evidence_summary":[""],"requires_user_confirmation":true,"execution_status":"draft_only"}
confidence 是 0 到 100 的整数。没有足够证据时 action 必须为 WATCH。
用户研究问题：""" + question + "\n证据包：\n" + json.dumps(packet, ensure_ascii=False)


def _validate_draft(value: object) -> dict:
    if not isinstance(value, dict):
        raise RuntimeError("Codex 未返回 JSON 决策草案")
    action = value.get("action")
    confidence = value.get("confidence")
    if action not in ALLOWED_ACTIONS or not isinstance(confidence, int) or not 0 <= confidence <= 100:
        raise RuntimeError("Codex 返回的草案字段无效")
    value["requires_user_confirmation"] = True
    value["execution_status"] = "draft_only"
    return value


def draft(question: str = "请审查当前公共 AI 模拟组合并给出下一步草案。") -> dict:
    packet = public_evidence_packet()
    with tempfile.TemporaryDirectory(prefix="public-ai-draft-") as workdir:
        completed = subprocess.run(
            [settings.codex_command, "exec", "--model", settings.public_ai_codex_model,
              "--config", f'model_reasoning_effort="{settings.public_ai_codex_reasoning_effort}"',
              "--sandbox", "read-only", "--skip-git-repo-check", "-C", workdir, "-"],
            input=_instruction(packet, question), capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=settings.public_ai_codex_timeout_seconds,
        )
    if completed.returncode:
        raise RuntimeError(completed.stderr[-1000:] or "公共 AI Codex 调用失败")
    try:
        result = _validate_draft(json.loads(completed.stdout.strip()))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Codex 返回的草案不是有效 JSON") from exc
    return {"draft": result, "evidence_generated_at": packet["evidence_generated_at"],
            "data_as_of": packet["data_through"], "ledger_mutated": False}
