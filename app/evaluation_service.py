"""Point-in-time historical data, news, and prediction evaluation.

All files owned by this service are isolated from production accounts and the
public AI ledger.  Inputs are append-only: an observation or news item is never
silently overwritten after import.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, time
from pathlib import Path

from app.config import settings


def _root() -> Path:
    root = Path(settings.evaluation_data_root).expanduser().resolve()
    for name in ("market", "news", "sessions"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def _read(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write_new(path: Path, value: dict) -> None:
    if path.exists():
        existing = _read(path, {})
        comparable = {key: item for key, item in value.items() if key != "imported_at"}
        existing_comparable = {key: item for key, item in existing.items() if key != "imported_at"}
        if existing_comparable != comparable:
            raise ValueError(f"immutable record already exists: {path.name}")
        return
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_available(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("available_at must be ISO-8601 with timezone") from exc
    if parsed.tzinfo is None:
        raise ValueError("available_at must include timezone")
    return parsed


def import_market(rows: list[dict]) -> dict:
    """Import daily closes. `available_at` means when this value became visible."""
    recorded = 0
    for raw in rows:
        instrument, as_of, close, available_at = (str(raw.get(key, "")) for key in
                                                   ("instrument", "as_of", "close", "available_at"))
        if not instrument or not as_of or not close:
            raise ValueError("instrument, as_of, close and available_at are required")
        _parse_available(available_at)
        try:
            float(close); datetime.fromisoformat(as_of)
        except ValueError as exc:
            raise ValueError("as_of must be YYYY-MM-DD and close numeric") from exc
        payload = {"schema_version": 1, "instrument": instrument, "as_of": as_of,
                   "close": close, "available_at": available_at,
                   "source": str(raw.get("source") or "unspecified"),
                   "imported_at": datetime.now().astimezone().isoformat(timespec="seconds")}
        _write_new(_root() / "market" / f"{instrument}_{as_of}.json", payload)
        recorded += 1
    return {"recorded": recorded}


def import_news(items: list[dict]) -> dict:
    """Import dated news. Undated news is rejected to prevent future leakage."""
    recorded = 0
    for raw in items:
        title, body, source, available_at = (str(raw.get(key, "")).strip() for key in
                                              ("title", "body", "source", "available_at"))
        if not title or not body or not source or not available_at:
            raise ValueError("title, body, source and available_at are required")
        _parse_available(available_at)
        canonical = "\n".join((source, title, body, str(raw.get("url") or ""), available_at))
        news_id = str(raw.get("news_id") or hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24])
        payload = {"schema_version": 1, "news_id": news_id, "title": title, "body": body,
                   "source": source, "url": str(raw.get("url") or ""),
                   "published_at": str(raw.get("published_at") or available_at), "available_at": available_at,
                   "entities": sorted({str(x) for x in raw.get("entities", []) if str(x)}),
                   "event_type": str(raw.get("event_type") or "other"),
                   "reliability": str(raw.get("reliability") or "secondary"),
                   "dedupe_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}
        _write_new(_root() / "news" / f"{news_id}.json", payload)
        recorded += 1
    return {"recorded": recorded}


def _visible(as_of: str) -> tuple[list[dict], list[dict]]:
    cutoff = datetime.combine(datetime.fromisoformat(as_of).date(), time.max).astimezone()
    market = [row for path in (_root() / "market").glob("*.json") for row in [_read(path, {})]
              if row.get("as_of", "") <= as_of and _parse_available(row["available_at"]) <= cutoff]
    news = [row for path in (_root() / "news").glob("*.json") for row in [_read(path, {})]
            if _parse_available(row["available_at"]) <= cutoff]
    return sorted(market, key=lambda x: (x["instrument"], x["as_of"])), sorted(news, key=lambda x: x["available_at"])


def create_session(as_of: str, instruments: list[str], initial_cash: str = "100000") -> dict:
    datetime.fromisoformat(as_of)
    market, news = _visible(as_of)
    allowed = set(instruments)
    market = [row for row in market if row["instrument"] in allowed]
    snapshot = {"schema_version": 1, "session_id": f"EV-{uuid.uuid4().hex[:12]}", "as_of": as_of,
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "initial_cash": str(initial_cash), "instruments": sorted(allowed),
                "market": market, "news": news, "predictions": []}
    _write_new(_root() / "sessions" / f"{snapshot['session_id']}.json", snapshot)
    return snapshot


def session(session_id: str) -> dict:
    path = _root() / "sessions" / f"{session_id}.json"
    if not path.exists():
        raise LookupError("evaluation session not found")
    return _read(path, {})


def record_prediction(session_id: str, prediction: dict) -> dict:
    state = session(session_id)
    required = ("instrument", "direction", "confidence", "horizon_trading_days")
    if any(prediction.get(key) in (None, "") for key in required):
        raise ValueError("instrument, direction, confidence and horizon_trading_days are required")
    if prediction["instrument"] not in state["instruments"]:
        raise ValueError("instrument is not in this isolated session")
    if prediction["direction"] not in {"UP", "DOWN", "NEUTRAL"}:
        raise ValueError("direction must be UP, DOWN or NEUTRAL")
    confidence = int(prediction["confidence"])
    horizon = int(prediction["horizon_trading_days"])
    if not 0 <= confidence <= 100 or horizon <= 0:
        raise ValueError("invalid confidence or horizon")
    record = {"prediction_id": f"P-{uuid.uuid4().hex[:12]}", "instrument": prediction["instrument"],
              "direction": prediction["direction"], "confidence": confidence, "horizon_trading_days": horizon,
              "expected_return_range_percent": prediction.get("expected_return_range_percent"),
              "rationale": str(prediction.get("rationale") or ""),
              "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    state["predictions"].append(record)
    (_root() / "sessions" / f"{session_id}.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def score(session_id: str) -> dict:
    state = session(session_id)
    all_market = [row for path in (_root() / "market").glob("*.json") for row in [_read(path, {})]]
    results = []
    for prediction in state["predictions"]:
        history = sorted((row for row in all_market if row.get("instrument") == prediction["instrument"]), key=lambda x: x["as_of"])
        start = next((row for row in history if row["as_of"] == state["as_of"]), None)
        future = [row for row in history if row["as_of"] > state["as_of"]]
        if start is None or len(future) < prediction["horizon_trading_days"]:
            results.append({"prediction_id": prediction["prediction_id"], "status": "PENDING_OUTCOME"})
            continue
        end = future[prediction["horizon_trading_days"] - 1]
        returned = (float(end["close"]) / float(start["close"]) - 1) * 100
        actual = "UP" if returned > 0 else "DOWN" if returned < 0 else "NEUTRAL"
        results.append({"prediction_id": prediction["prediction_id"], "status": "SCORED", "target_as_of": end["as_of"],
                        "return_percent": round(returned, 4), "actual_direction": actual,
                        "direction_hit": actual == prediction["direction"]})
    scored = [row for row in results if row["status"] == "SCORED"]
    return {"session_id": session_id, "as_of": state["as_of"], "results": results,
            "metrics": {"prediction_count": len(results), "scored_count": len(scored),
                        "direction_hit_rate": round(sum(row["direction_hit"] for row in scored) / len(scored), 4) if scored else None}}
