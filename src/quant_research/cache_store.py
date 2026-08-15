from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from app.config import settings

DB_PATH = Path(settings.database_root).expanduser().resolve() / "market_cache.sqlite3"
_lock = threading.RLock()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS cache_entries ("
        "cache_key TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, updated_at REAL NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS search_history ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, query TEXT NOT NULL, searched_at REAL NOT NULL)"
    )
    connection.commit()
    return connection


def get_cache(key: str, max_age_seconds: int | None = None) -> tuple[object | None, bool]:
    """Return (payload, fresh). A stale payload is returned with fresh=False."""
    with _lock, _connect() as connection:
        row = connection.execute(
            "SELECT payload, updated_at FROM cache_entries WHERE cache_key = ?", (key,)
        ).fetchone()
    if row is None:
        return None, False
    fresh = max_age_seconds is None or time.time() - row[1] <= max_age_seconds
    return json.loads(row[0]), fresh


def same_refresh_window(updated_at: float, now: float, interval_seconds: int) -> bool:
    """True when two timestamps are inside the same wall-clock-aligned interval."""
    if interval_seconds <= 0:
        return False
    return int(updated_at // interval_seconds) == int(now // interval_seconds)


def get_aligned_cache(key: str, interval_seconds: int) -> tuple[object | None, bool]:
    """Read cache using aligned windows, e.g. 10:00-10:09:59 for 600 seconds."""
    with _lock, _connect() as connection:
        row = connection.execute(
            "SELECT payload, updated_at FROM cache_entries WHERE cache_key = ?", (key,)
        ).fetchone()
    if row is None:
        return None, False
    return json.loads(row[0]), same_refresh_window(row[1], time.time(), interval_seconds)


def get_aligned_caches_by_prefix(prefix: str, interval_seconds: int) -> dict[str, object]:
    """Read a family of fresh cache entries with one SQLite connection."""
    now = time.time()
    with _lock, _connect() as connection:
        rows = connection.execute(
            "SELECT cache_key,payload,updated_at FROM cache_entries WHERE cache_key LIKE ?", (f"{prefix}%",)
        ).fetchall()
    return {key: json.loads(payload) for key, payload, updated_at in rows
            if same_refresh_window(updated_at, now, interval_seconds)}


def put_cache(key: str, kind: str, payload: object) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with _lock, _connect() as connection:
        connection.execute(
            "INSERT INTO cache_entries(cache_key, kind, payload, updated_at) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(cache_key) DO UPDATE SET kind=excluded.kind, payload=excluded.payload, updated_at=excluded.updated_at",
            (key, kind, serialized, time.time()),
        )
        connection.commit()


def record_search(category: str, query: str) -> None:
    with _lock, _connect() as connection:
        connection.execute(
            "INSERT INTO search_history(category, query, searched_at) VALUES(?, ?, ?)",
            (category, query.strip(), time.time()),
        )
        connection.commit()


def cache_stats() -> dict:
    with _lock, _connect() as connection:
        counts = dict(connection.execute("SELECT kind, COUNT(*) FROM cache_entries GROUP BY kind").fetchall())
        searches = connection.execute("SELECT COUNT(*) FROM search_history").fetchone()[0]
    return {"database": str(DB_PATH), "entries": counts, "searches": searches}
