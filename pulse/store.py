"""SQLite persistence for sites and probe results."""

from __future__ import annotations

import math
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL UNIQUE,
    url       TEXT NOT NULL,
    created   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id     INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    ts          TEXT NOT NULL,
    ok          INTEGER NOT NULL,
    status_code INTEGER,
    latency_ms  REAL,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS checks_site_ts ON checks(site_id, ts DESC);
"""


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Site:
    id: int
    name: str
    url: str
    created: str


@dataclass(frozen=True)
class Check:
    id: int
    site_id: int
    ts: str
    ok: bool
    status_code: int | None
    latency_ms: float | None
    error: str | None


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile of an ascending list; ``None`` when the list is empty."""
    if not sorted_values:
        return None
    rank = max(1, math.ceil(pct / 100 * len(sorted_values)))
    return sorted_values[rank - 1]


class Store:
    """Thread-safe wrapper around one SQLite file (or ``:memory:``)."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # -- sites -------------------------------------------------------------
    def add_site(self, name: str, url: str) -> Site:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO sites(name, url, created) VALUES (?, ?, ?)",
                (name, url, utcnow()),
            )
            self._conn.commit()
            return self.get_site(cur.lastrowid)  # type: ignore[arg-type]

    def get_site(self, site_id: int) -> Site | None:
        row = self._conn.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
        return Site(**dict(row)) if row else None

    def get_site_by_name(self, name: str) -> Site | None:
        row = self._conn.execute("SELECT * FROM sites WHERE name = ?", (name,)).fetchone()
        return Site(**dict(row)) if row else None

    def list_sites(self) -> list[Site]:
        rows = self._conn.execute("SELECT * FROM sites ORDER BY id").fetchall()
        return [Site(**dict(r)) for r in rows]

    def delete_site(self, site_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # -- checks ------------------------------------------------------------
    def record_check(
        self,
        site_id: int,
        ok: bool,
        status_code: int | None,
        latency_ms: float | None,
        error: str | None = None,
        ts: str | None = None,
    ) -> Check:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO checks(site_id, ts, ok, status_code, latency_ms, error) VALUES (?, ?, ?, ?, ?, ?)",
                (site_id, ts or utcnow(), int(ok), status_code, latency_ms, error),
            )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM checks WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _check(row)

    def recent_checks(self, site_id: int | None = None, limit: int = 50) -> list[Check]:
        if site_id is None:
            rows = self._conn.execute("SELECT * FROM checks ORDER BY ts DESC, id DESC LIMIT ?", (limit,)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM checks WHERE site_id = ? ORDER BY ts DESC, id DESC LIMIT ?",
                (site_id, limit),
            ).fetchall()
        return [_check(r) for r in rows]

    def summary(self, window: int = 100) -> list[dict]:
        """Per-site uptime and mean latency over the last ``window`` checks."""
        out: list[dict] = []
        for site in self.list_sites():
            checks = self.recent_checks(site.id, limit=window)
            total = len(checks)
            up = sum(1 for c in checks if c.ok)
            latencies = [c.latency_ms for c in checks if c.ok and c.latency_ms is not None]
            ordered = sorted(latencies)
            p50 = _percentile(ordered, 50)
            p95 = _percentile(ordered, 95)
            out.append(
                {
                    "site_id": site.id,
                    "name": site.name,
                    "url": site.url,
                    "checks": total,
                    "uptime_pct": round(100.0 * up / total, 2) if total else None,
                    "mean_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
                    "p50_latency_ms": round(p50, 1) if p50 is not None else None,
                    "p95_latency_ms": round(p95, 1) if p95 is not None else None,
                    "last_ok": checks[0].ok if checks else None,
                    "last_ts": checks[0].ts if checks else None,
                }
            )
        return out

    def seed(self, sites: Iterable[tuple[str, str]]) -> None:
        for name, url in sites:
            if not self.get_site_by_name(name):
                self.add_site(name, url)


def _check(row: sqlite3.Row) -> Check:
    d = dict(row)
    d["ok"] = bool(d["ok"])
    return Check(**d)
