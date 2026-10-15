"""Penyimpanan SQLite: pair, candle harian, dan hasil pemindaian.

Cukup SQLite untuk skala ini (ratusan pair x candle harian). Semua skema
dibuat idempoten lewat init_db() supaya file lama bisa dipakai lagi.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

_lock = threading.Lock()


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def init(self) -> None:
        with _lock, self.connect() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS pairs(
                    pair_id TEXT PRIMARY KEY,
                    symbol TEXT, name TEXT, traded_currency TEXT,
                    coingecko_id TEXT, logo TEXT, updated_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS candles(
                    pair_id TEXT NOT NULL, ts INTEGER NOT NULL,
                    open REAL, high REAL, low REAL, close REAL, volume REAL,
                    PRIMARY KEY(pair_id, ts)
                );
                CREATE INDEX IF NOT EXISTS idx_candles_pair_ts
                    ON candles(pair_id, ts);
                CREATE TABLE IF NOT EXISTS scans(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    horizon_days INTEGER, status TEXT, stage TEXT,
                    started_at INTEGER, finished_at INTEGER,
                    total INTEGER DEFAULT 0, analyzed INTEGER DEFAULT 0,
                    skipped INTEGER DEFAULT 0, message TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS results(
                    scan_id INTEGER NOT NULL, pair_id TEXT NOT NULL,
                    horizon_days INTEGER NOT NULL, score REAL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY(scan_id, pair_id)
                );
                CREATE INDEX IF NOT EXISTS idx_results_scan_score
                    ON results(scan_id, score DESC);
                """
            )

    # ------------------------------------------------------------- pairs
    def upsert_pairs(self, pairs: Iterable[dict], updated_at: int) -> None:
        rows = [
            (
                p["pair_id"], p.get("symbol", ""), p.get("name", ""),
                p.get("traded_currency", ""), p.get("coingecko_id", ""),
                p.get("logo", ""), updated_at,
            )
            for p in pairs
        ]
        with _lock, self.connect() as c:
            c.executemany(
                """INSERT INTO pairs(pair_id,symbol,name,traded_currency,coingecko_id,logo,updated_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(pair_id) DO UPDATE SET symbol=excluded.symbol,
                     name=excluded.name, traded_currency=excluded.traded_currency,
                     coingecko_id=excluded.coingecko_id, logo=excluded.logo,
                     updated_at=excluded.updated_at""",
                rows,
            )

    def pair(self, pair_id: str) -> dict | None:
        with self.connect() as c:
            row = c.execute("SELECT * FROM pairs WHERE pair_id=?", (pair_id,)).fetchone()
        return dict(row) if row else None

    def pairs(self) -> list[dict]:
        with self.connect() as c:
            rows = c.execute("SELECT * FROM pairs ORDER BY pair_id").fetchall()
        return [dict(r) for r in rows]

    # ----------------------------------------------------------- candles
    def upsert_candles(self, pair_id: str, candles: Iterable[dict]) -> int:
        rows = [
            (pair_id, int(k["ts"]), float(k["open"]), float(k["high"]),
             float(k["low"]), float(k["close"]), float(k.get("volume", 0.0)))
            for k in candles
            if float(k.get("close", 0)) > 0
        ]
        if not rows:
            return 0
        with _lock, self.connect() as c:
            c.executemany(
                """INSERT INTO candles(pair_id,ts,open,high,low,close,volume)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(pair_id,ts) DO UPDATE SET open=excluded.open,
                     high=excluded.high, low=excluded.low, close=excluded.close,
                     volume=excluded.volume""",
                rows,
            )
        return len(rows)

    def candles(self, pair_id: str, limit_days: int | None = None) -> list[dict]:
        sql = "SELECT * FROM candles WHERE pair_id=? ORDER BY ts"
        params: list[Any] = [pair_id]
        if limit_days:
            sql = ("SELECT * FROM (SELECT * FROM candles WHERE pair_id=? "
                   "ORDER BY ts DESC LIMIT ?) ORDER BY ts")
            params = [pair_id, int(limit_days)]
        with self.connect() as c:
            rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def candle_freshness(self, pair_id: str) -> tuple[int, int]:
        """(jumlah candle, ts candle terakhir)."""
        with self.connect() as c:
            row = c.execute(
                "SELECT COUNT(*) n, COALESCE(MAX(ts),0) last FROM candles WHERE pair_id=?",
                (pair_id,),
            ).fetchone()
        return int(row["n"]), int(row["last"])

    # ------------------------------------------------------------- scans
    def create_scan(self, horizon_days: int, started_at: int) -> int:
        with _lock, self.connect() as c:
            cur = c.execute(
                "INSERT INTO scans(horizon_days,status,stage,started_at) VALUES(?,?,?,?)",
                (horizon_days, "running", "memulai", started_at),
            )
            return int(cur.lastrowid)

    def update_scan(self, scan_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with _lock, self.connect() as c:
            c.execute(f"UPDATE scans SET {cols} WHERE id=?", (*fields.values(), scan_id))

    def scan(self, scan_id: int) -> dict | None:
        with self.connect() as c:
            row = c.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
        return dict(row) if row else None

    def latest_scan(self, horizon_days: int | None = None, *,
                    selesai_saja: bool = False) -> dict | None:
        sql = "SELECT * FROM scans"
        conds: list[str] = []
        params: list[Any] = []
        if horizon_days is not None:
            conds.append("horizon_days=?")
            params.append(horizon_days)
        if selesai_saja:
            conds.append("status IN ('done','error')")
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY id DESC LIMIT 1"
        with self.connect() as c:
            row = c.execute(sql, params).fetchone()
        return dict(row) if row else None

    def get_scan(self, scan_id: int | None) -> dict | None:
        if scan_id is None:
            return None
        return self.scan(scan_id)

    def latest_done_scan(self, horizon_days: int) -> dict | None:
        with self.connect() as c:
            row = c.execute(
                "SELECT * FROM scans WHERE horizon_days=? AND status='done' "
                "ORDER BY id DESC LIMIT 1", (horizon_days,)).fetchone()
        return dict(row) if row else None

    def scans_between(self, start_ts: int, end_ts: int) -> list[dict]:
        """Pemindaian yang dimulai dalam rentang [start_ts, end_ts)."""
        with self.connect() as c:
            rows = c.execute(
                "SELECT * FROM scans WHERE started_at>=? AND started_at<? "
                "ORDER BY started_at", (int(start_ts), int(end_ts))).fetchall()
        return [dict(r) for r in rows]

    def list_scans(self, limit: int = 10) -> list[dict]:
        with self.connect() as c:
            rows = c.execute(
                "SELECT * FROM scans ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    def scan_results(self, scan_id: int) -> list[dict]:
        return self.results(scan_id)

    def result_for_pair(self, scan_id: int, pair_id: str) -> dict | None:
        return self.result(scan_id, pair_id)

    def save_result(self, scan_id: int, payload: dict) -> None:
        with _lock, self.connect() as c:
            c.execute(
                "INSERT OR REPLACE INTO results(scan_id,pair_id,horizon_days,score,payload) "
                "VALUES(?,?,?,?,?)",
                (scan_id, payload["pair_id"], int(payload["horizon_days"]),
                 float(payload["score"]), json.dumps(payload)),
            )

    def results(self, scan_id: int) -> list[dict]:
        with self.connect() as c:
            rows = c.execute(
                "SELECT payload FROM results WHERE scan_id=? ORDER BY score DESC",
                (scan_id,),
            ).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def result(self, scan_id: int, pair_id: str) -> dict | None:
        with self.connect() as c:
            row = c.execute(
                "SELECT payload FROM results WHERE scan_id=? AND pair_id=?",
                (scan_id, pair_id),
            ).fetchone()
        return json.loads(row["payload"]) if row else None
