"""The broker's own state, in SQLite (a volume only the broker mounts):

- terminals: one row per user: container / volume names, its private API key, state, last use, disk use;
- leases: which user had which IP in terminal-net, when (to read the egress proxy log by user);
- events: evictions, busy refusals, memory kills, quota refusals (counters for the monitor).
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS terminals (
    user_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    name TEXT NOT NULL,
    volume TEXT,
    api_key TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'absent',
    created REAL NOT NULL,
    last_used REAL NOT NULL,
    disk_kb INTEGER NOT NULL DEFAULT 0,
    over_quota INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS leases (
    ip TEXT NOT NULL,
    user_id TEXT NOT NULL,
    start REAL NOT NULL,
    end REAL
);
CREATE INDEX IF NOT EXISTS leases_ip ON leases (ip, start);
CREATE TABLE IF NOT EXISTS events (
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    user_id TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
"""


@dataclass
class Terminal:
    user_hash: str
    user_id: str
    role: str
    name: str
    volume: Optional[str]
    api_key: str
    state: str  # running | stopped | absent (no container)
    created: float
    last_used: float
    disk_kb: int = 0
    over_quota: int = 0


class Store:
    def __init__(self, path: str):
        if path != ':memory:':
            os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def _run(self, sql: str, args: tuple = ()) -> list:
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
            self._db.commit()
            return rows

    # ------------------------------------------------------------ terminals

    def get(self, user_hash: str) -> Optional[Terminal]:
        rows = self._run('SELECT * FROM terminals WHERE user_hash = ?', (user_hash,))
        return Terminal(**dict(rows[0])) if rows else None

    def all(self) -> list[Terminal]:
        return [Terminal(**dict(r)) for r in self._run('SELECT * FROM terminals ORDER BY last_used DESC')]

    def save(self, t: Terminal) -> None:
        self._run(
            """INSERT INTO terminals (user_hash, user_id, role, name, volume, api_key, state, created, last_used, disk_kb,
                                      over_quota)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_hash) DO UPDATE SET user_id = excluded.user_id, role = excluded.role,
                   name = excluded.name, volume = excluded.volume, api_key = excluded.api_key, state = excluded.state,
                   last_used = excluded.last_used, disk_kb = excluded.disk_kb, over_quota = excluded.over_quota""",
            (t.user_hash, t.user_id, t.role, t.name, t.volume, t.api_key, t.state, t.created, t.last_used, t.disk_kb,
             t.over_quota),
        )

    def delete(self, user_hash: str) -> None:
        self._run('DELETE FROM terminals WHERE user_hash = ?', (user_hash,))

    # ------------------------------------------------------------ leases

    def lease(self, ip: str, user_id: str, now: Optional[float] = None) -> None:
        now = now or time.time()
        self._run('UPDATE leases SET end = ? WHERE ip = ? AND end IS NULL', (now, ip))
        self._run('INSERT INTO leases (ip, user_id, start) VALUES (?, ?, ?)', (ip, user_id, now))

    def release(self, user_id: str, now: Optional[float] = None) -> None:
        self._run('UPDATE leases SET end = ? WHERE user_id = ? AND end IS NULL', (now or time.time(), user_id))

    def leases_since(self, since: float) -> list[dict]:
        rows = self._run('SELECT * FROM leases WHERE end IS NULL OR end >= ? ORDER BY start', (since,))
        return [dict(r) for r in rows]

    def prune_leases(self, before: float) -> None:
        self._run('DELETE FROM leases WHERE end IS NOT NULL AND end < ?', (before,))

    # ------------------------------------------------------------ events

    def event(self, kind: str, user_id: Optional[str] = None, detail: str = '') -> None:
        self._run('INSERT INTO events (ts, kind, user_id, detail) VALUES (?, ?, ?, ?)',
                  (time.time(), kind, user_id, detail[:500]))

    def counts(self, since: float) -> dict[str, int]:
        rows = self._run('SELECT kind, count(*) AS n FROM events WHERE ts >= ? GROUP BY kind', (since,))
        return {r['kind']: r['n'] for r in rows}

    def recent_events(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self._run('SELECT * FROM events ORDER BY ts DESC LIMIT ?', (limit,))]

    def prune_events(self, before: float) -> None:
        self._run('DELETE FROM events WHERE ts < ?', (before,))
