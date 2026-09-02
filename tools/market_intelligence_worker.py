"""Run 10-minute quant refreshes and an hourly DeepSeek research refresh."""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Allow `python scripts/market_intelligence_worker.py` from the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Local market-data providers are unreliable through the desktop proxy.  Keep
# this process direct without changing the user's global proxy configuration.
os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

from src.quant_research.intelligence import refresh_intelligence, refresh_quant_snapshot


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Generate one dated research packet and exit")
    args = parser.parse_args()
    last_quant_window: int | None = None
    last_llm_window: int | None = None
    one_shot_failed = False
    while True:
            now = datetime.now().astimezone()
            quant_window = int(now.timestamp() // 600)
            quant = None
            if quant_window != last_quant_window:
                try:
                    quant = refresh_quant_snapshot()
                    print(f"{now.isoformat(timespec='seconds')} quant snapshot refreshed", flush=True)
                except Exception as exc:
                    print(f"{now.isoformat(timespec='seconds')} quant refresh failed: {exc}", flush=True)
                    one_shot_failed = True
                last_quant_window = quant_window
            llm_window = int(now.timestamp() // 3600)
            if llm_window != last_llm_window:
                try:
                    packet = refresh_intelligence(quant)
                    print(f"{packet['generated_at']} DeepSeek intelligence refreshed", flush=True)
                except Exception as exc:
                    print(f"{now.isoformat(timespec='seconds')} intelligence refresh failed: {exc}", flush=True)
                    one_shot_failed = True
                last_llm_window = llm_window
                if args.once:
                    raise SystemExit(1 if one_shot_failed else 0)
            time.sleep(20)


if __name__ == "__main__":
    main()
