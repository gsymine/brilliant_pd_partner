"""SQLite persistence: the backend's source of truth and the agents' long-term memory.

ZooWork keeps each Session's conversation log, but has no public memory-store API, so
everything an agent learns (vendor facts, offers, reports) is written here through custom tools.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id              TEXT PRIMARY KEY,
    status          TEXT NOT NULL,
    title           TEXT NOT NULL,
    brief           TEXT NOT NULL,
    brief_items     TEXT NOT NULL,          -- JSON list of {sku, name, requirements}
    required_fields TEXT NOT NULL,          -- JSON list of checklist field names
    costing         TEXT,                   -- JSON {forecast, assumptions} or null
    deadline_at     TEXT NOT NULL,
    created_by      TEXT,
    error           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vendor_threads (
    id               TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES runs(id),
    vendor_name      TEXT NOT NULL,
    vendor_email     TEXT NOT NULL,
    country          TEXT,
    status           TEXT NOT NULL,
    close_reason     TEXT,
    outbound_count   INTEGER NOT NULL DEFAULT 0,
    reminders_sent   INTEGER NOT NULL DEFAULT 0,
    last_outbound_at TEXT,
    last_inbound_at  TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_threads_run ON vendor_threads(run_id);
CREATE TABLE IF NOT EXISTS emails (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL,
    thread_id   TEXT NOT NULL REFERENCES vendor_threads(id),
    direction   TEXT NOT NULL,              -- in | out
    message_id  TEXT,
    in_reply_to TEXT,
    references_ TEXT,
    from_addr   TEXT,
    to_addr     TEXT,
    subject     TEXT,
    body        TEXT,
    attachments TEXT NOT NULL DEFAULT '[]', -- JSON list of {id, filename, content_type, size, path}
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_emails_thread ON emails(thread_id);
CREATE INDEX IF NOT EXISTS ix_emails_msgid ON emails(message_id);
CREATE TABLE IF NOT EXISTS vendor_facts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id       TEXT NOT NULL REFERENCES vendor_threads(id),
    product_ref     TEXT NOT NULL,
    field           TEXT NOT NULL,
    value           TEXT,                   -- JSON-encoded value
    source_email_id TEXT,
    quote           TEXT,
    confidence      REAL,
    version         INTEGER NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_facts_thread ON vendor_facts(thread_id);
CREATE TABLE IF NOT EXISTS offers (
    id         TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL REFERENCES runs(id),
    thread_id  TEXT NOT NULL REFERENCES vendor_threads(id),
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_offers_run ON offers(run_id);
CREATE TABLE IF NOT EXISTS reports (
    run_id     TEXT PRIMARY KEY REFERENCES runs(id),
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    run_id             TEXT PRIMARY KEY REFERENCES runs(id),
    outcome            TEXT NOT NULL,
    accepted_offer_ids TEXT NOT NULL,
    notes              TEXT,
    decided_by         TEXT,
    decided_at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agents (
    role        TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_sessions (
    role       TEXT NOT NULL,               -- communication | extraction | analysis
    owner_id   TEXT NOT NULL,               -- thread id (communication) or run id
    run_id     TEXT NOT NULL,
    agent_id   TEXT NOT NULL,
    session_id TEXT NOT NULL,
    cursor     TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (role, owner_id)
);
CREATE TABLE IF NOT EXISTS tool_calls (
    call_id    TEXT PRIMARY KEY,
    role       TEXT NOT NULL,
    session_id TEXT NOT NULL,
    name       TEXT NOT NULL,
    input      TEXT,
    result     TEXT,
    is_error   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL,
    thread_id  TEXT,
    kind       TEXT NOT NULL,
    message    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_run_events_run ON run_events(run_id);
"""

JSON_COLUMNS = {"brief_items", "required_fields", "costing", "attachments", "data", "accepted_offer_ids", "input", "result"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out: dict[str, Any] = {}
    for key in row.keys():
        value = row[key]
        if key in JSON_COLUMNS and isinstance(value, str):
            value = json.loads(value)
        out[key] = value
    return out


class Database:
    """Thin, thread-safe wrapper. All writes are short; one lock is enough for this backend."""

    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    # ---- generic helpers -------------------------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        with self._lock:
            self._conn.execute(sql, tuple(params))
            self._conn.commit()

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        with self._lock:
            return _decode(self._conn.execute(sql, tuple(params)).fetchone())

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [_decode(r) for r in self._conn.execute(sql, tuple(params)).fetchall()]  # type: ignore[misc]

    def insert(self, table: str, row: dict[str, Any]) -> None:
        cols = list(row)
        values = [json.dumps(v) if k in JSON_COLUMNS and v is not None else v for k, v in row.items()]
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
        self.execute(sql, values)

    def update(self, table: str, key: str, key_value: Any, changes: dict[str, Any]) -> None:
        if not changes:
            return
        if table in {"runs", "vendor_threads"}:
            changes = {**changes, "updated_at": now_iso()}
        sets = ", ".join(f"{c} = ?" for c in changes)
        values = [json.dumps(v) if k in JSON_COLUMNS and v is not None else v for k, v in changes.items()]
        self.execute(f"UPDATE {table} SET {sets} WHERE {key} = ?", [*values, key_value])

    # ---- runs ------------------------------------------------------------------------------
    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM runs WHERE id = ?", [run_id])

    def list_runs(self) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM runs ORDER BY created_at DESC")

    def runs_with_status(self, *statuses: str) -> list[dict[str, Any]]:
        marks = ", ".join("?" for _ in statuses)
        return self.all(f"SELECT * FROM runs WHERE status IN ({marks})", statuses)

    def log(self, run_id: str, kind: str, message: str, thread_id: str | None = None) -> None:
        self.insert("run_events", {
            "run_id": run_id, "thread_id": thread_id, "kind": kind,
            "message": message[:2000], "created_at": now_iso(),
        })

    def run_events(self, run_id: str, limit: int = 200) -> list[dict[str, Any]]:
        return self.all(
            "SELECT * FROM run_events WHERE run_id = ? ORDER BY id DESC LIMIT ?", [run_id, limit]
        )

    # ---- vendor threads --------------------------------------------------------------------
    def get_thread(self, thread_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM vendor_threads WHERE id = ?", [thread_id])

    def threads_for_run(self, run_id: str) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM vendor_threads WHERE run_id = ? ORDER BY created_at", [run_id])

    # ---- emails ----------------------------------------------------------------------------
    def emails_for_thread(self, thread_id: str) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM emails WHERE thread_id = ? ORDER BY created_at, rowid", [thread_id])

    def get_email(self, email_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM emails WHERE id = ?", [email_id])

    def email_by_message_id(self, message_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM emails WHERE message_id = ?", [message_id])

    # ---- vendor memory (facts) -------------------------------------------------------------
    def add_fact(self, thread_id: str, product_ref: str, field: str, value: Any,
                 source_email_id: str | None, quote: str | None, confidence: float | None) -> int:
        """Append-only: a new value for the same (product, field) becomes a new version."""
        with self._lock:
            prev = self._conn.execute(
                "SELECT MAX(version) FROM vendor_facts WHERE thread_id = ? AND product_ref = ? AND field = ?",
                (thread_id, product_ref, field),
            ).fetchone()[0]
            version = (prev or 0) + 1
            self._conn.execute(
                "INSERT INTO vendor_facts (thread_id, product_ref, field, value, source_email_id, quote, "
                "confidence, version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (thread_id, product_ref, field, json.dumps(value), source_email_id, quote,
                 confidence, version, now_iso()),
            )
            self._conn.commit()
            return version

    def facts_for_thread(self, thread_id: str, latest_only: bool = True) -> list[dict[str, Any]]:
        rows = self.all(
            "SELECT * FROM vendor_facts WHERE thread_id = ? ORDER BY product_ref, field, version", [thread_id]
        )
        for r in rows:
            r["value"] = json.loads(r["value"]) if r["value"] is not None else None
        if not latest_only:
            return rows
        latest: dict[tuple[str, str], dict[str, Any]] = {}
        for r in rows:
            latest[(r["product_ref"], r["field"])] = r
        return list(latest.values())

    # ---- offers / reports / decisions ------------------------------------------------------
    def offers_for_run(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.all("SELECT * FROM offers WHERE run_id = ? ORDER BY created_at, rowid", [run_id])
        return [{"id": r["id"], "thread_id": r["thread_id"], **r["data"]} for r in rows]

    def replace_offers(self, run_id: str, offers: list[dict[str, Any]]) -> list[str]:
        ids: list[str] = []
        with self._lock:
            self._conn.execute("DELETE FROM offers WHERE run_id = ?", (run_id,))
            for offer in offers:
                oid = new_id("off")
                data = {k: v for k, v in offer.items() if k not in {"id", "thread_id"}}
                self._conn.execute(
                    "INSERT INTO offers (id, run_id, thread_id, data, created_at) VALUES (?, ?, ?, ?, ?)",
                    (oid, run_id, offer["thread_id"], json.dumps(data), now_iso()),
                )
                ids.append(oid)
            self._conn.commit()
        return ids

    def get_report(self, run_id: str) -> dict[str, Any] | None:
        row = self.one("SELECT * FROM reports WHERE run_id = ?", [run_id])
        return row["data"] if row else None

    def save_report(self, run_id: str, data: dict[str, Any]) -> None:
        self.execute(
            "INSERT INTO reports (run_id, data, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET data = excluded.data, created_at = excluded.created_at",
            [run_id, json.dumps(data), now_iso()],
        )

    def get_decision(self, run_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM decisions WHERE run_id = ?", [run_id])

    # ---- agents & sessions -----------------------------------------------------------------
    def get_agent(self, role: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM agents WHERE role = ?", [role])

    def save_agent(self, role: str, agent_id: str, config_hash: str) -> None:
        self.execute(
            "INSERT INTO agents (role, agent_id, config_hash, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(role) DO UPDATE SET agent_id = excluded.agent_id, "
            "config_hash = excluded.config_hash, updated_at = excluded.updated_at",
            [role, agent_id, config_hash, now_iso()],
        )

    def get_session(self, role: str, owner_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM agent_sessions WHERE role = ? AND owner_id = ?", [role, owner_id])

    def session_by_id(self, session_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM agent_sessions WHERE session_id = ?", [session_id])

    def save_session(self, role: str, owner_id: str, run_id: str, agent_id: str, session_id: str) -> None:
        self.insert("agent_sessions", {
            "role": role, "owner_id": owner_id, "run_id": run_id, "agent_id": agent_id,
            "session_id": session_id, "cursor": None, "created_at": now_iso(),
        })

    def save_cursor(self, session_id: str, cursor: str) -> None:
        self.execute("UPDATE agent_sessions SET cursor = ? WHERE session_id = ?", [cursor, session_id])

    # ---- custom tool call idempotency ------------------------------------------------------
    def get_tool_call(self, call_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM tool_calls WHERE call_id = ?", [call_id])

    def save_tool_call(self, call_id: str, role: str, session_id: str, name: str,
                       tool_input: Any, result: Any, is_error: bool) -> None:
        self.execute(
            "INSERT OR REPLACE INTO tool_calls (call_id, role, session_id, name, input, result, is_error, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [call_id, role, session_id, name, json.dumps(tool_input), json.dumps(result), int(is_error), now_iso()],
        )
