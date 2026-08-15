"""Collect timestamped RSS news into the isolated evaluation store.

RSS is suitable for continuing collection.  Historical backfill should use the
NDJSON importer because it preserves the original source timestamp and URL.
"""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.evaluation_service import import_news


def _text(item, name: str) -> str:
    node = item.find(name)
    return (node.text or "").strip() if node is not None and node.text else ""


def _parse(url: str) -> list[dict]:
    request = Request(url, headers={"User-Agent": "market-evaluation-collector/1.0"})
    with urlopen(request, timeout=30) as response:
        root = ET.fromstring(response.read())
    rows = []
    for item in root.findall(".//item"):
        title, body, published = _text(item, "title"), _text(item, "description"), _text(item, "pubDate")
        if not title or not body or not published:
            continue
        try:
            available = parsedate_to_datetime(published)
        except (TypeError, ValueError):
            continue
        if available.tzinfo is None:
            continue
        rows.append({"source": url, "url": _text(item, "link"), "title": title, "body": body,
                     "published_at": available.isoformat(), "available_at": available.isoformat(),
                     "event_type": "rss", "reliability": "secondary"})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", action="append", required=True, help="RSS URL; repeatable")
    args = parser.parse_args()
    rows = []
    for url in args.url:
        rows.extend(_parse(url))
    print(import_news(rows))


if __name__ == "__main__":
    main()
