"""SQLite storage. One file, no setup. Products are stored by vendor x product,
with the extracted fields as JSON keyed by app.fields.FIELD_KEYS."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(os.environ.get("PD_DB_PATH", Path(__file__).resolve().parent.parent / "var" / "pd_partner.db"))

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS vendors (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, country TEXT, contact_name TEXT, email TEXT,
  categories TEXT, rating REAL, history TEXT
);
CREATE TABLE IF NOT EXISTS rounds (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, theme TEXT, requirements TEXT, categories TEXT,
  deadline TEXT, launch_date TEXT, threshold REAL DEFAULT 0.7, status TEXT DEFAULT 'collecting'
);
CREATE TABLE IF NOT EXISTS round_vendors (
  round_id INTEGER, vendor_id TEXT,
  invited_at TEXT, replied_at TEXT, -- status is computed, see app.main.vendor_status
  PRIMARY KEY (round_id, vendor_id)
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY, round_id INTEGER, vendor_id TEXT,
  direction TEXT NOT NULL,         -- in | out
  kind TEXT NOT NULL,              -- rfq | reply | followup | reminder | decision
  subject TEXT, body TEXT,
  status TEXT NOT NULL,            -- pending_approval | sent | received
  created_at TEXT, sent_at TEXT
);
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY, round_id INTEGER, vendor_id TEXT,
  fields TEXT NOT NULL,            -- JSON {field_key: value}
  low_confidence TEXT DEFAULT '[]',-- JSON [field_key]
  status TEXT NOT NULL,            -- extracted | saved
  scores TEXT,                     -- JSON {dim: 0-100} or null
  decision TEXT,                   -- null = use suggestion | accept | modify | reject
  change_request TEXT
);
CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY, round_id INTEGER, ts TEXT, level TEXT, text TEXT
);
"""

JSON_COLS = {"fields", "low_confidence", "scores"}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with connect() as c:
        c.executescript(SCHEMA_SQL)


def row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS & d.keys():
        d[k] = json.loads(d[k]) if d[k] else None
    return d


def rows(rs) -> list[dict]:
    return [row(r) for r in rs]


def log(c: sqlite3.Connection, round_id: int, text: str, level: str = "info") -> None:
    c.execute("INSERT INTO activity (round_id, ts, level, text) VALUES (?,?,?,?)", (round_id, now(), level, text))
