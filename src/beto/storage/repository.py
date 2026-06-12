"""Persistência leve em SQLite: dedup de oportunidades + histórico de coletas.

stdlib `sqlite3` puro — sem ORM. O loop é single-threaded (asyncio), então o
acesso é naturalmente serializado.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict

from beto.arbitrage.engine import ArbOpportunity
from beto.scrapers.base import ScrapeResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities (
    dedup_key   TEXT PRIMARY KEY,
    event_key   TEXT NOT NULL,
    market      TEXT NOT NULL,
    profit_pct  REAL NOT NULL,
    payload     TEXT NOT NULL,
    first_seen  REAL NOT NULL,
    last_seen   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_opps_last_seen ON opportunities(last_seen);

CREATE TABLE IF NOT EXISTS scrape_runs (
    run_at     REAL NOT NULL,
    house      TEXT NOT NULL,
    ok         INTEGER NOT NULL,
    n_quotes   INTEGER NOT NULL,
    duration_s REAL NOT NULL,
    error      TEXT
);
"""


class OpportunityRepo:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def seen_recently(self, dedup_key: str, ttl_s: int) -> bool:
        row = self._conn.execute(
            "SELECT last_seen FROM opportunities WHERE dedup_key = ?", (dedup_key,)
        ).fetchone()
        return row is not None and (time.time() - row[0]) < ttl_s

    def mark_seen(self, opp: ArbOpportunity) -> None:
        now = time.time()
        payload = json.dumps(asdict(opp), default=str, ensure_ascii=False)
        self._conn.execute(
            """
            INSERT INTO opportunities
                (dedup_key, event_key, market, profit_pct, payload, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dedup_key) DO UPDATE SET
                profit_pct = excluded.profit_pct,
                payload = excluded.payload,
                last_seen = excluded.last_seen
            """,
            (
                opp.dedup_key(),
                opp.event_key,
                f"{opp.market_type.value}:{opp.line}",
                opp.profit_pct,
                payload,
                now,
                now,
            ),
        )
        self._conn.commit()

    def record_runs(self, results: list[ScrapeResult]) -> None:
        now = time.time()
        self._conn.executemany(
            "INSERT INTO scrape_runs (run_at, house, ok, n_quotes, duration_s, error)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                (now, r.house, int(r.ok), len(r.quotes), r.duration_s, r.error)
                for r in results
            ],
        )
        self._conn.commit()

    def opportunities_today(self) -> int:
        day_start = time.time() - 86_400
        row = self._conn.execute(
            "SELECT COUNT(*) FROM opportunities WHERE last_seen >= ?", (day_start,)
        ).fetchone()
        return int(row[0])

    def close(self) -> None:
        self._conn.close()
