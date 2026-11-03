"""SQLite cache of API responses with TTL (≤ cache.ttl_days_max, default 30 days — YouTube API policy)."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .clock import now_utc

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS evidence (
  ref TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT, published_at TEXT, author TEXT,
  channel TEXT, snippet TEXT, kind TEXT, created_at REAL NOT NULL
);
"""


class Store:
    """Shared SQLite connection for the cache and the evidence registry."""

    def __init__(self, path: Path, ttl_days_max: int = 30):
        self.path = path
        self.ttl_max_s = max(1, int(ttl_days_max)) * 86400
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.purge()

    def _now(self) -> float:
        return now_utc().timestamp()

    def get(self, key: str) -> Any | None:
        with self._lock:
            row = self.conn.execute("SELECT value, expires_at FROM cache WHERE key=?", (key,)).fetchone()
        if not row or row[1] < self._now():
            return None
        return json.loads(row[0])

    def set(self, key: str, value: Any, ttl_s: int) -> None:
        ttl = min(int(ttl_s), self.ttl_max_s)
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO cache(key, value, expires_at) VALUES (?,?,?)",
                (key, json.dumps(value, ensure_ascii=False), self._now() + ttl),
            )
            self.conn.commit()

    def purge(self) -> None:
        now = self._now()
        with self._lock:
            self.conn.execute("DELETE FROM cache WHERE expires_at < ?", (now,))
            self.conn.execute("DELETE FROM evidence WHERE created_at < ?", (now - self.ttl_max_s,))
            self.conn.commit()
