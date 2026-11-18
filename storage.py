"""Small SQLite queue and append-only audit store; use a durable queue/database in deployment."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str | Path):
        self.db = sqlite3.connect(str(path), timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS events (
              event_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
              payload TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
              next_attempt_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_ready ON events(status, next_attempt_at);
            CREATE TABLE IF NOT EXISTS account_states (
              account_token TEXT PRIMARY KEY, expiry_month INTEGER, expiry_year INTEGER,
              state_version TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS dead_letters (
              event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit (
              audit_id INTEGER PRIMARY KEY, event_id TEXT NOT NULL, action TEXT NOT NULL,
              details TEXT NOT NULL, at TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
        """)
        self.db.commit()

    def enqueue(self, event: dict[str, Any]) -> bool:
        now = utcnow()
        cur = self.db.execute("INSERT OR IGNORE INTO events(event_id,idempotency_key,payload,status,created_at,updated_at) VALUES(?,?,?,'RECEIVED',?,?)",
                              (event["event_id"], event["idempotency_key"], json.dumps(event, sort_keys=True), now, now))
        if cur.rowcount:
            self.audit(event["event_id"], "ENQUEUED", {})
            self.db.commit()
            return True
        return False

    def save_account_state(self, state: dict[str, Any], state_version: str) -> None:
        """Save the internal tokenized current state; never accept a PAN field."""
        if set(state) - {"account_token", "expiry_month", "expiry_year"}:
            raise ValueError("Account state may contain only token and expiry fields")
        if not state.get("account_token") or not state_version:
            raise ValueError("account_token and state_version are required")
        self.db.execute("""
            INSERT INTO account_states(account_token,expiry_month,expiry_year,state_version,updated_at)
            VALUES(?,?,?,?,?) ON CONFLICT(account_token) DO UPDATE SET
              expiry_month=excluded.expiry_month, expiry_year=excluded.expiry_year,
              state_version=excluded.state_version, updated_at=excluded.updated_at
        """, (state["account_token"], state.get("expiry_month"), state.get("expiry_year"), state_version, utcnow()))
        self.audit("account:" + state["account_token"], "ACCOUNT_STATE_UPDATED", {"state_version": state_version})
        self.db.commit()

    def get_account_state(self, account_token: str) -> dict[str, Any] | None:
        row = self.db.execute("""
            SELECT account_token,expiry_month,expiry_year,state_version,updated_at
            FROM account_states WHERE account_token=?
        """, (account_token,)).fetchone()
        return dict(row) if row else None

    def audit(self, event_id: str, action: str, details: dict[str, Any]) -> None:
        # Callers must keep details token-only and free of raw bank payloads.
        self.db.execute("INSERT INTO audit(event_id,action,details,at) VALUES(?,?,?,?)",
                        (event_id, action, json.dumps(details, sort_keys=True), utcnow()))

    def transition(self, event_id: str, status: str, action: str, details: dict[str, Any] | None = None) -> None:
        self.db.execute("UPDATE events SET status=?,updated_at=? WHERE event_id=?", (status, utcnow(), event_id))
        self.audit(event_id, action, details or {})
        self.db.commit()

    def retry_or_dead_letter(self, event_id: str, reason: str, max_attempts: int = 5) -> str:
        row = self.db.execute("SELECT payload,attempts FROM events WHERE event_id=?", (event_id,)).fetchone()
        if not row:
            raise KeyError(event_id)
        attempts = row["attempts"] + 1
        if attempts >= max_attempts:
            status = "DEAD_LETTER"
            self.db.execute("INSERT OR REPLACE INTO dead_letters VALUES(?,?,?,?)", (event_id, row["payload"], reason, utcnow()))
        else:
            status = "RETRY"
            delay = min(3600, 2 ** attempts)
            self.db.execute("UPDATE events SET attempts=?,status=?,next_attempt_at=datetime('now', ?),updated_at=? WHERE event_id=?",
                            (attempts, status, f"+{delay} seconds", utcnow(), event_id))
        if status == "DEAD_LETTER":
            self.db.execute("UPDATE events SET attempts=?,status=?,updated_at=? WHERE event_id=?", (attempts, status, utcnow(), event_id))
        self.audit(event_id, status, {"reason": reason, "attempt": attempts})
        self.db.commit()
        return status

    def close(self) -> None:
        self.db.close()

    def metrics(self, sla_seconds: int = 3600) -> dict[str, int]:
        """Compact SLA view; pending events older than the threshold count as overdue."""
        row = self.db.execute("""
            SELECT COUNT(*) AS total,
              SUM(CASE WHEN status IN ('RECEIVED','RETRY','RELEASED') THEN 1 ELSE 0 END) AS pending,
              SUM(CASE WHEN status IN ('RECEIVED','RETRY','RELEASED') AND julianday(created_at) < julianday('now', ?) THEN 1 ELSE 0 END) AS overdue,
              SUM(CASE WHEN status='DEAD_LETTER' THEN 1 ELSE 0 END) AS dead_letters
            FROM events
        """, (f"-{sla_seconds} seconds",)).fetchone()
        return {key: int(row[key] or 0) for key in ("total", "pending", "overdue", "dead_letters")}

    def replay(self, event_id: str, acknowledged: bool = False, approved: bool = False) -> dict[str, Any]:
        """Return the original immutable event for deterministic replay; never auto-resend."""
        if acknowledged and not approved:
            raise PermissionError("Replay of an acknowledged event requires approval")
        row = self.db.execute("SELECT payload FROM events WHERE event_id=?", (event_id,)).fetchone()
        if not row:
            raise KeyError(event_id)
        self.audit(event_id, "REPLAY_REQUESTED", {"acknowledged": acknowledged, "approved": approved})
        self.db.commit()
        return json.loads(row["payload"])
