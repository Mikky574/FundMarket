"""Refresh read-only market research evidence for the QQ/Codex bridge.

This command may update only files below ``market_intelligence``.  It never
imports the paper ledger engine and cannot settle orders, record decisions, or
modify ``paper/state.json``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.market_intelligence import refresh_intelligence, refresh_quant_snapshot


def main() -> None:
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
        # Quantitative evidence is still usable; preserve the failure so the
        # caller can disclose that the narrative summary was not refreshed.
        result["research_summary_error"] = str(exc)[:300]
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
