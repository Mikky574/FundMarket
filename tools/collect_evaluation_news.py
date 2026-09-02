"""Import manually reviewed historical news into the isolated evaluation store.

Input must be UTF-8 JSON Lines.  Every line requires source, title, body and
available_at with a timezone.  This refuses undated news because it cannot be
safely replayed without leaking future information.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.historical_evaluation.service import import_news


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True, help="UTF-8 NDJSON news file")
    args = parser.parse_args()
    items = []
    for number, line in enumerate(args.input.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid JSON on line {number}: {exc}") from exc
    print(import_news(items))


if __name__ == "__main__":
    main()
