"""Run the approved historical-data collectors from one local source manifest.

The manifest contains only public identifiers and RSS URLs.  Secrets belong in
environment configuration and are never read by this script.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def _run(args: list[str]) -> dict:
    completed = subprocess.run([sys.executable, *args], cwd=ROOT.parent, text=True,
                               capture_output=True, encoding="utf-8", errors="replace")
    return {"command": args[0], "ok": completed.returncode == 0,
            "stdout": completed.stdout.strip(), "error": completed.stderr.strip()[-500:]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", type=Path, default=ROOT / "evaluation_sources.json")
    args = parser.parse_args()
    if not args.sources.exists():
        raise SystemExit(f"source manifest not found: {args.sources}; copy evaluation_sources.example.json first")
    source = json.loads(args.sources.read_text(encoding="utf-8"))
    start, end = source.get("start"), source.get("end")
    funds = [str(code) for code in source.get("funds", [])]
    if not start or not end or not funds:
        raise SystemExit("manifest requires start, end and at least one fund")
    market_args = ["scripts/collect_evaluation_market.py", "--start", start, "--end", end]
    for code in funds:
        market_args.extend(["--fund", code])
    result = {"funds": _run(market_args)}
    benchmark = source.get("benchmark")
    if benchmark:
        index_args = ["scripts/collect_evaluation_indexes.py", "--start", start, "--end", end,
                      "--benchmark", str(benchmark)]
        for industry in source.get("industries", []):
            index_args.extend(["--industry", str(industry)])
        result["indexes"] = _run(index_args)
    urls = [str(url) for url in source.get("rss_urls", []) if str(url)]
    if urls:
        rss_args = ["scripts/collect_evaluation_rss.py"]
        for url in urls:
            rss_args.extend(["--url", url])
        result["rss"] = _run(rss_args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if any(not item["ok"] for item in result.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
