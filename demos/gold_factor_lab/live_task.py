"""Independent JD ZheShang live-data collector.

This process is intentionally separate from the web app, QQ bridge, ledger and
evaluation engine.  It stores only raw market observations in a local SQLite
file so the eventual research dataset has an auditable collection trail.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from demos.gold_factor_lab.collector import collect_jd_intraday, collect_jd_latest


DEFAULT_DATABASE = Path("data/gold_lab/gold_live.sqlite3")


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY,
            source TEXT NOT NULL,
            source_at TEXT NOT NULL,
            retrieved_at TEXT NOT NULL,
            value REAL NOT NULL,
            unit TEXT NOT NULL,
            availability_basis TEXT NOT NULL,
            raw_json TEXT NOT NULL,
            UNIQUE(source, source_at, retrieved_at, value)
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS collector_health (
            source TEXT PRIMARY KEY,
            last_success_at TEXT,
            last_error_at TEXT,
            last_error_kind TEXT
        )
    """)
    return connection


def _record(connection: sqlite3.Connection, row: dict) -> None:
    connection.execute(
        """INSERT OR IGNORE INTO observations
        (source, source_at, retrieved_at, value, unit, availability_basis, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (row["source"], row["source_at"], row["retrieved_at"], float(row["value"]), row["unit"],
         row["availability_basis"], json.dumps(row, ensure_ascii=False, separators=(",", ":"))),
    )


def _mark_success(connection: sqlite3.Connection, source: str, timestamp: str) -> None:
    connection.execute(
        """INSERT INTO collector_health(source, last_success_at, last_error_at, last_error_kind)
        VALUES (?, ?, NULL, NULL)
        ON CONFLICT(source) DO UPDATE SET last_success_at=excluded.last_success_at,
            last_error_at=NULL, last_error_kind=NULL""",
        (source, timestamp),
    )


def _mark_error(connection: sqlite3.Connection, source: str, timestamp: str, error: Exception) -> None:
    connection.execute(
        """INSERT INTO collector_health(source, last_success_at, last_error_at, last_error_kind)
        VALUES (?, NULL, ?, ?)
        ON CONFLICT(source) DO UPDATE SET last_error_at=excluded.last_error_at,
            last_error_kind=excluded.last_error_kind""",
        (source, timestamp, type(error).__name__),
    )


def collect_once(connection: sqlite3.Connection, *, include_intraday: bool = True) -> dict:
    """Collect an exact product quote plus current-day intraday points once."""
    result = {"latest_saved": 0, "intraday_saved": 0, "errors": []}
    try:
        row = collect_jd_latest()
        _record(connection, row)
        _mark_success(connection, row["source"], row["retrieved_at"])
        result["latest_saved"] = 1
    except Exception as exc:  # Keep a transient upstream failure out of the ledger and task scheduler.
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        _mark_error(connection, "jd_zheshang_latest", now, exc)
        result["errors"].append("jd_zheshang_latest")
    if include_intraday:
        try:
            rows = collect_jd_intraday()
            for row in rows:
                _record(connection, row)
            _mark_success(connection, "jd_zheshang_public_intraday_chart", rows[-1]["retrieved_at"])
            result["intraday_saved"] = len(rows)
        except Exception as exc:
            now = datetime.now().astimezone().isoformat(timespec="seconds")
            _mark_error(connection, "jd_zheshang_public_intraday_chart", now, exc)
            result["errors"].append("jd_zheshang_public_intraday_chart")
    connection.commit()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--interval-seconds", type=float, default=15.0)
    parser.add_argument("--intraday-every", type=int, default=4,
                        help="Fetch the full current-day minute chart every N quote polls.")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    if args.interval_seconds < 5:
        raise SystemExit("interval must be at least 5 seconds")
    if args.intraday_every < 1:
        raise SystemExit("intraday-every must be positive")
    with _connect(args.database) as connection:
        iteration = 0
        while True:
            result = collect_once(connection, include_intraday=(iteration % args.intraday_every == 0))
            if not args.quiet:
                print(json.dumps(result, ensure_ascii=False), flush=True)
            if args.once:
                return
            iteration += 1
            time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
