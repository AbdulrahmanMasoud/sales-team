"""SQLite storage. One short-lived connection per operation keeps it simple and thread-safe."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS customers (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '');

CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
  product TEXT NOT NULL, amount REAL NOT NULL CHECK(amount >= 0),
  payment TEXT NOT NULL CHECK(payment IN ('cod','online')),
  status TEXT NOT NULL CHECK(status IN ('new','confirmed','shipped','delivered','returned','cancelled')),
  source TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, delivered_at TEXT);
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, agent TEXT NOT NULL, trigger TEXT NOT NULL, status TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT, summary TEXT NOT NULL DEFAULT '',
  turns INTEGER, cost_usd REAL, input_tokens INTEGER, output_tokens INTEGER, error TEXT);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, run_id INTEGER, agent TEXT NOT NULL, kind TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY, from_agent TEXT NOT NULL, to_agent TEXT NOT NULL, body TEXT NOT NULL,
  created_at TEXT NOT NULL, read_at TEXT, run_id INTEGER);

CREATE TABLE IF NOT EXISTS memory (
  id INTEGER PRIMARY KEY, agent TEXT NOT NULL, fact TEXT NOT NULL,
  created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY, agent TEXT NOT NULL, run_id INTEGER, kind TEXT NOT NULL,
  title TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', hypothesis TEXT NOT NULL DEFAULT '',
  metric TEXT NOT NULL DEFAULT '', check_date TEXT, cost_note TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL DEFAULT '{}', mode TEXT NOT NULL, status TEXT NOT NULL,
  created_at TEXT NOT NULL, resolved_at TEXT, executed_at TEXT, exec_note TEXT NOT NULL DEFAULT '',
  outcome TEXT NOT NULL DEFAULT '', verdict TEXT);
CREATE INDEX IF NOT EXISTS idx_decisions_status ON decisions(status);

CREATE TABLE IF NOT EXISTS budget_changes (
  id INTEGER PRIMARY KEY, object_id TEXT NOT NULL, old_budget REAL, new_budget REAL,
  created_at TEXT NOT NULL, decision_id INTEGER, dry_run INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS followups (
  id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id), order_id INTEGER,
  segment TEXT NOT NULL CHECK(segment IN ('confirm','delivered','repeat','lapsed')),
  cand_key TEXT NOT NULL, message TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('draft','sent','skipped')),
  created_at TEXT NOT NULL, sent_at TEXT, run_id INTEGER);
CREATE INDEX IF NOT EXISTS idx_followups_key ON followups(cand_key);

CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, unit_cost REAL NOT NULL CHECK(unit_cost >= 0),
  updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def today() -> str:
    return datetime.now().astimezone().date().isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(config.db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with connect() as c:
        c.execute("PRAGMA journal_mode = WAL")
        c.executescript(SCHEMA)
        _add_missing_columns(c)


def _add_missing_columns(c: sqlite3.Connection) -> None:
    """A database made by an older version keeps working: any column added since then is added here.
    Existing rows get the column's default (or an empty value), and nothing is ever dropped or rewritten."""
    ref = sqlite3.connect(":memory:")
    try:
        ref.executescript(SCHEMA)
        for (table,) in ref.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
            have = {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
            for _cid, name, ctype, notnull, default, _pk in ref.execute(f"PRAGMA table_info({table})").fetchall():
                if name in have:
                    continue
                if default is None and notnull:
                    default = "0" if ctype.upper() in ("INTEGER", "REAL") else "''"
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ctype}"
                          + (f" NOT NULL DEFAULT {default}" if notnull else (f" DEFAULT {default}" if default is not None else "")))
    finally:
        ref.close()


def rows(sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    with connect() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def one(sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
    with connect() as c:
        r = c.execute(sql, params).fetchone()
        return dict(r) if r else None


def execute(sql: str, params: tuple | list = ()) -> int:
    with connect() as c:
        cur = c.execute(sql, params)
        return cur.lastrowid or cur.rowcount


def update(sql: str, params: tuple | list = ()) -> int:
    """Run an UPDATE/DELETE and return how many rows changed (used for atomic state changes)."""
    with connect() as c:
        return c.execute(sql, params).rowcount


def log_event(agent: str, kind: str, title: str = "", body: Any = "", run_id: int | None = None) -> None:
    if not isinstance(body, str):
        body = json.dumps(body, ensure_ascii=False, default=str)
    execute(
        "INSERT INTO events(run_id, agent, kind, title, body, created_at) VALUES (?,?,?,?,?,?)",
        (run_id, agent, kind, title[:300], body[:4000], now()),
    )
