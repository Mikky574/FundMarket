"""Scheduled, read-only market intelligence for the QQ/Codex research flow.

This module never imports the paper ledger and deliberately contains no trade or
decision operation.  Its output is dated research evidence, not an instruction.
"""
from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.request import Request, urlopen

import httpx

from src.config import settings
from src.quant_research.fund_data import get_fund_overview
from src.quant_research.fund_signals import fund_research_card
from src.quant_research.providers.akshare_provider import AkShareProvider
from src.quant_research.stock_service import StockService
from src.paths import PUBLIC_FUND_POOL_PATH, PUBLIC_LEDGER_DATA_ROOT, PUBLIC_LEDGER_STATE_PATH


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _root() -> Path:
    root = Path(settings.market_intelligence_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _read_key() -> str:
    if settings.deepseek_api_key.strip():
        return settings.deepseek_api_key.strip()
    path = Path(settings.deepseek_api_key_file)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def _parse_published_at(value: str) -> datetime | None:
    """Parse the common RSS timestamps without guessing a date from the title."""
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone()
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
        except ValueError:
            return None


def collect_news(now: datetime | None = None) -> list[dict]:
    """Fetch recent, deduplicated RSS evidence; stale headlines never reach the LLM."""
    now = now or datetime.now().astimezone()
    cutoff = now - timedelta(hours=48)
    entries: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for raw_url in filter(None, (item.strip() for item in settings.market_news_rss_urls.split(","))):
        try:
            request = Request(raw_url, headers={"User-Agent": "market-research/1.0"})
            with urlopen(request, timeout=15) as response:  # nosec B310 - operator config
                root = ET.fromstring(response.read())
            for item in root.findall(".//item")[:12]:
                title = (item.findtext("title") or "").strip()
                published_at = (item.findtext("pubDate") or "").strip()
                published = _parse_published_at(published_at)
                key = (title.casefold(), (item.findtext("link") or "").strip())
                if title and published and published >= cutoff and key not in seen:
                    seen.add(key)
                    entries.append({"title": title, "published_at": published.isoformat(timespec="seconds"),
                                    "url": key[1], "source": raw_url})
        except Exception as exc:
            entries.append({"source": raw_url, "error": str(exc)[:160]})
    return sorted(entries, key=lambda row: row.get("published_at", ""), reverse=True)[:30]


def _watch_funds(funds: list[tuple[str, str]], industry_rows: list[dict] | None = None) -> list[dict]:
    """Watch every active public fund and disclosed component stock.

    Fund reports are disclosures, not live holdings; their report date is kept in
    every record so the LLM cannot present the components as current facts.
    """
    quote_service = StockService(AkShareProvider())
    watched: list[dict] = []
    for code, fund_name in funds:
        fund_live: dict = {}
        try:
            overview = get_fund_overview(code, force_refresh=True)
            fund_live = {"latest": overview.get("latest"), "returns": overview.get("returns"),
                         "max_drawdown_one_year": overview.get("max_drawdown_one_year"),
                         "source": "public_fund_nav"}
            fund_live["research_card"] = fund_research_card(overview, industry_rows or [])
        except Exception as exc:
            fund_live = {"source": "public_fund_nav", "error": str(exc)[:160]}
        snapshots = sorted(PUBLIC_LEDGER_DATA_ROOT.glob(f"*fund_{code}*.json"), key=lambda item: item.stat().st_mtime)
        disclosure = {}
        # Prefer the newest report that actually contains disclosed components;
        # a later official-NAV snapshot is not a holdings report.
        for path in reversed(snapshots):
            candidate = json.loads(path.read_text(encoding="utf-8"))
            if candidate.get("holdings"):
                disclosure = candidate
                break
        components = []
        for item in disclosure.get("holdings", [])[:15]:
            component = {"symbol": item.get("symbol"), "name": item.get("name"), "weight_percent": item.get("weight"),
                         "disclosed_trend": item.get("trend")}
            try:
                quote = quote_service.quote(str(item.get("symbol", "")))
                component["quote"] = quote.model_dump(mode="json") if quote else None
            except Exception as exc:
                component["quote_error"] = str(exc)[:120]
            components.append(component)
        watched.append({"fund_code": code, "fund_name": fund_name,
                        "disclosure_report": disclosure.get("holding_report"),
                        "disclosure_data_through": disclosure.get("data_through"),
                        "fund_live": fund_live,
                        "components": components,
                        "component_data_caveat": "成分股来自最近披露报告，可能滞后于基金当前实际持仓。" if components
                        else "现有资料没有可核实的成分股明细；仅跟踪该基金本身。"})
    return watched


def collect_portfolio_watchlist(industry_rows: list[dict] | None = None) -> list[dict]:
    state_path = PUBLIC_LEDGER_STATE_PATH
    if not state_path.exists():
        return []
    state = json.loads(state_path.read_text(encoding="utf-8"))
    funds = [(code, position.get("name", code)) for code, position in state.get("positions", {}).items()
             if any(float(lot.get("shares_remaining", 0)) > 0 for lot in position.get("lots", []))]
    return _watch_funds(funds, industry_rows)


def collect_public_research_watchlist(industry_rows: list[dict] | None = None) -> list[dict]:
    """Public AI candidate/observation pool, including unheld technology funds."""
    path = PUBLIC_FUND_POOL_PATH
    if not path.exists():
        return []
    pool = json.loads(path.read_text(encoding="utf-8"))
    funds = [(str(item["code"]), item.get("name", str(item["code"]))) for item in pool.get("funds", [])
             if "WATCH" in str(item.get("pool_status", ""))]
    return _watch_funds(funds, industry_rows)


def _watch_registry(*groups: tuple[str, list[dict]]) -> dict:
    """Persistent registry of public positions and public research candidates."""
    entries: dict[str, dict] = {}
    for source, rows in groups:
        for row in rows:
            item = entries.setdefault(row["fund_code"], {**row, "sources": []})
            item["sources"].append(source)
    return {"generated_at": _now(), "research_only": True,
            "entries": [{**item, "sources": sorted(set(item["sources"]))} for item in entries.values()],
            "privacy": "只含公共 AI 持仓和公共研究候选；不含任何用户账户来源、金额或份额。"}


def _write_watch_registry(registry: dict) -> None:
    path = _root() / "watchlist.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _prune_quant_snapshots(keep_days: int = 14) -> None:
    """Retain a short reproducibility window without allowing endless cache growth."""
    cutoff = datetime.now().astimezone() - timedelta(days=keep_days)
    for path in (_root() / "quant").glob("*.json"):
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
            if modified < cutoff:
                path.unlink()
        except OSError:
            # Retention must never make an evidence refresh fail.
            continue


def run_quant_snapshot() -> dict:
    """Run the existing reproducible industry scorer and retain its immutable output."""
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    output = _root() / "quant" / f"{timestamp}.json"
    script = Path("scripts/market_snapshot.py").resolve()
    completed = subprocess.run([sys.executable, str(script), "--output", str(output),
                                "--max-industries", str(settings.market_intelligence_max_industries)],
                               cwd=Path.cwd(), capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=300)
    if completed.returncode:
        raise RuntimeError(completed.stderr[-800:] or "quant snapshot failed")
    return json.loads(output.read_text(encoding="utf-8"))


def refresh_quant_snapshot() -> dict:
    """Refresh raw quantitative evidence without an LLM call (10-minute job)."""
    quant = run_quant_snapshot()
    quant["portfolio_watchlist"] = collect_portfolio_watchlist(quant.get("industries", []))
    quant["public_research_watchlist"] = collect_public_research_watchlist(quant.get("industries", []))
    # Persist deterministic candidates with the quant snapshot so independent
    # evaluators can consume them without importing application code.
    quant["opportunity_candidates"] = opportunity_candidates(quant)
    registry = _watch_registry(
        ("public_position", quant["portfolio_watchlist"]),
        ("public_research_pool", quant["public_research_watchlist"]),
    )
    quant["watch_registry_generated_at"] = registry["generated_at"]
    _write_watch_registry(registry)
    path = _root() / "latest_quant.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(quant, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    _prune_quant_snapshots()
    return quant


def refresh_public_market_display() -> dict:
    """Refresh research artifacts allowed for the local QQ bridge only.

    This deliberately excludes paper ledger settlement, valuation recording,
    decision recording, and all order operations.
    """
    quant = refresh_quant_snapshot()
    result = {
        "quant_snapshot_at": quant.get("snapshot_at"),
        "data_through": quant.get("data_through"),
        "candidate_count": quant.get("candidate_count"),
        "research_summary_refreshed": False,
    }
    try:
        intelligence = refresh_intelligence(quant)
        result["research_summary_refreshed"] = True
        result["generated_at"] = intelligence.get("generated_at")
    except Exception as exc:
        result["research_summary_error"] = str(exc)[:300]
    return result


def latest_quant_snapshot() -> dict:
    path = _root() / "latest_quant.json"
    if not path.exists():
        raise RuntimeError("quant snapshot is not available")
    return json.loads(path.read_text(encoding="utf-8"))


def opportunity_candidates(quant: dict, limit: int = 6) -> list[dict]:
    """Rank *research candidates*, never price predictions or trading signals.

    The score rewards independent confirmations that a current move may persist,
    while deliberately deducting volatility and crowded recent moves.  The output
    contains the conditions required to validate or invalidate the hypothesis.
    """
    benchmark = quant.get("benchmark", {})
    benchmark_20d = float(benchmark.get("return_20d") or 0)
    candidates: list[dict] = []
    for row in quant.get("industries", []):
        signal = row.get("signals", {})
        score = row.get("scores", {})
        r20 = float(row.get("return_20d") or 0)
        r5 = float(row.get("return_5d") or 0)
        relative = float(signal.get("relative_return_20d", r20 - benchmark_20d) or 0)
        breadth = float(signal.get("breadth_percent") or 0)
        volume = float(row.get("volume_ratio_20d") or 0)
        volatility = float(signal.get("volatility_20d") or 0)
        drawdown = abs(float(signal.get("drawdown_60d") or 0))
        trend_state = signal.get("trend_state", "mixed")
        # 0..100: trend and relative strength matter most; risk controls prevent
        # a recent spike from automatically becoming a candidate.
        continuation = (0.30 * float(score.get("trend", 0)) +
                        0.25 * float(score.get("relative_strength", 0)) +
                        0.20 * min(100, breadth) +
                        0.10 * min(100, volume * 50) +
                        0.15 * float(score.get("risk_control", 0)))
        crowding_penalty = max(0, abs(r5) - 7) * 2 + max(0, r20 - 15) * 1.5
        risk_penalty = max(0, volatility - 35) * 0.35 + max(0, drawdown - 12) * 0.5
        opportunity = round(max(0, min(100, continuation - crowding_penalty - risk_penalty)), 1)
        if trend_state == "downtrend" or opportunity < 45:
            continue
        confirmation = ["行业收盘继续位于 20 日均线之上", "20 日相对沪深300收益保持为正"]
        if breadth >= 60:
            confirmation.append("行业上涨家数占比维持在 60% 以上")
        if volume >= 1:
            confirmation.append("成交量不低于 20 日均量")
        invalidation = ["行业收盘跌破 20 日均线", "20 日相对沪深300收益转负"]
        if volatility > 35 or abs(r5) > 7:
            invalidation.append("高波动或短期急涨后出现放量回落")
        candidates.append({
            "industry": row.get("name"), "research_score_0_100": opportunity,
            "research_window": "未来 5–20 个交易日的观察窗口，不是收益预测",
            "hypothesis": "趋势、相对强弱、行业参与度与风险控制共同支持延续性观察。",
            "quant_evidence": {"trend_state": trend_state, "return_5d_percent": r5,
                              "return_20d_percent": r20, "relative_return_20d_percent": relative,
                              "breadth_percent": breadth, "volume_ratio_20d": volume,
                              "volatility_20d_percent": volatility, "drawdown_60d_percent": -drawdown},
            "confirmation_conditions": confirmation,
            "invalidation_conditions": invalidation,
            "risk_flags": (["短期涨幅或波动偏高，存在拥挤/回撤风险"] if abs(r5) > 7 or volatility > 35 else []),
            "data_confidence": row.get("data_confidence"),
        })
    return sorted(candidates, key=lambda item: item["research_score_0_100"], reverse=True)[:limit]


NEWS_INTERPRETATION_CONTRACT = """
News interpretation is required when news is present. In addition to the existing fields, return a
`news_interpretation` object with `overall_tone` (positive, negative, mixed, neutral, or insufficient_data),
`important_items` (at most 5 items with headline, source, published_at, affected_industries, market_signal,
significance, and verification_note), and `ignored_count`. Select only concrete market, policy, macro, company,
or industry implications. Do not infer that a headline caused a price move. A single media report remains an
unverified clue unless it links to an official disclosure. Keep raw news as evidence but do not repeat all of it.
"""


def _prompt(quant: dict, news: list[dict]) -> str:
    industries = quant.get("industries", [])[:12]
    compact = [{key: row.get(key) for key in ("name", "daily_return", "return_5d", "return_20d", "return_60d", "leader")}
               | {"scores": row.get("scores"), "data_confidence": row.get("data_confidence")} for row in industries]
    evidence = {"generated_at": _now(), "quant_snapshot_at": quant.get("snapshot_at"),
                "data_through": quant.get("data_through"), "benchmark": quant.get("benchmark"),
                "market_regime": quant.get("market_regime"),
                "top_industries": compact, "public_portfolio_watchlist": quant.get("portfolio_watchlist", []),
                "public_research_watchlist": quant.get("public_research_watchlist", []),
                "opportunity_candidates": opportunity_candidates(quant), "news": news,
                "news_interpretation_contract": NEWS_INTERPRETATION_CONTRACT}
    return """你是市场研究摘要器。仅依据下列 JSON 证据写中文摘要，不得补造事实、价格、新闻或来源；这不是投资建议，也不是交易指令。\n
返回严格 JSON：{\"summary\": string, \"highlights\": [string], \"risks\": [string], \"watch_items\": [string], \"data_caveats\": [string], \"opportunity_outlook\": [{\"industry\": string, \"outlook\": \"观察优先|等待确认|不宜追高\", \"why_now\": string, \"event_support\": string, \"counter_evidence\": string, \"next_check\": string}]}。\n
要求：opportunity_outlook 只能解释 evidence.opportunity_candidates 内的行业，最多 3 个；它们是未来 5–20 个交易日的可证伪研究假设，绝不能写成上涨预测或买卖建议。若新闻没有直接、可信的对应催化，event_support 必须写“未发现可核验的直接催化”。区分量化事实与推断；新闻为空时明确说明未配置或未取得新闻；不建议买卖，不提及账本操作。\n证据：\n""" + json.dumps(evidence, ensure_ascii=False)


def analyse(quant: dict, news: list[dict]) -> dict:
    key = _read_key()
    if not key:
        raise RuntimeError("DeepSeek API key is not configured")
    response = httpx.post("https://api.deepseek.com/chat/completions", headers={"Authorization": f"Bearer {key}"},
                          json={"model": "deepseek-v4-flash", "messages": [
                              {"role": "system", "content": "Return valid JSON. Preserve summary, highlights, risks, watch_items, data_caveats, and opportunity_outlook. opportunity_outlook may only discuss supplied candidates and must be falsifiable, not a forecast or trade instruction. When news is present, also include news_interpretation with overall_tone, important_items (at most 5), and ignored_count. Do not repeat every headline; select only material, dated items and distinguish inference from verified fact."},
                              {"role": "user", "content": _prompt(quant, news)}],
                                "thinking": {"type": "disabled"}, "response_format": {"type": "json_object"},
                                # The structured opportunity outlook adds several required
                                # fields.  Keep enough room for valid JSON instead of
                                # silently accepting a truncated response.
                                "temperature": 0.2, "max_tokens": 3200},
                          timeout=settings.market_intelligence_http_timeout_seconds)
    response.raise_for_status()
    content = response.json()["choices"][0]["message"].get("content") or "{}"
    result = json.loads(content)
    if not isinstance(result, dict) or not isinstance(result.get("summary"), str) or not result["summary"].strip():
        raise RuntimeError("DeepSeek returned an empty market summary")
    return result


def refresh_intelligence(quant: dict | None = None) -> dict:
    """Create the hourly LLM interpretation from the most recent quant evidence."""
    quant = quant or latest_quant_snapshot()
    news = collect_news()
    interpretation = analyse(quant, news)
    packet = {"generated_at": _now(), "model": "deepseek-v4-flash", "research_only": True,
              "quant_snapshot_at": quant.get("snapshot_at"), "data_through": quant.get("data_through"),
              "benchmark": quant.get("benchmark"), "market_regime": quant.get("market_regime"),
              "top_industries": quant.get("industries", [])[:12],
              "portfolio_watchlist": quant.get("portfolio_watchlist", []),
              "public_research_watchlist": quant.get("public_research_watchlist", []),
              "opportunity_candidates": opportunity_candidates(quant),
              "news": news, "interpretation": interpretation,
              "limitations": ["量化结果和语言摘要仅供研究，不构成交易指令。", "公共 AI 组合仍须先向用户展示决策草案并获得明确确认后才可入账。"]}
    path = _root() / "latest.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return packet


def latest() -> dict:
    path = _root() / "latest.json"
    if not path.exists():
        return {"available": False, "reason": "市场情报尚未生成"}
    packet = json.loads(path.read_text(encoding="utf-8"))
    return {"available": True, **packet}


def watchlist() -> dict:
    path = _root() / "watchlist.json"
    if not path.exists():
        return {"available": False, "reason": "公共关注仓尚未生成"}
    return {"available": True, **json.loads(path.read_text(encoding="utf-8"))}
