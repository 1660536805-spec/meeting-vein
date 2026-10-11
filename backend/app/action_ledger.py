"""Durable, versioned human action tracking linked to existing board nodes."""
from __future__ import annotations

import sqlite3
import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path


class ActionConflict(RuntimeError):
    """The caller edited an outdated version of an action."""


class ActionLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS actions (
                    id TEXT PRIMARY KEY,
                    meeting_id TEXT NOT NULL,
                    action_node_id TEXT NOT NULL,
                    decision_node_id TEXT,
                    event_id TEXT NOT NULL,
                    owner TEXT,
                    due_date TEXT,
                    status TEXT NOT NULL DEFAULT 'open',
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(meeting_id, event_id),
                    UNIQUE(meeting_id, action_node_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS actions_meeting ON actions(meeting_id)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS action_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    meeting_id TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    before_json TEXT,
                    after_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS action_events_action ON action_events(meeting_id, action_id)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS action_external_sync (
                    meeting_id TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    synced_version INTEGER,
                    payload_fingerprint TEXT,
                    attempted_at TEXT NOT NULL,
                    error_code TEXT,
                    PRIMARY KEY(meeting_id, action_id)
                )
            """)
            sync_columns = {row["name"] for row in conn.execute("PRAGMA table_info(action_external_sync)")}
            if "payload_fingerprint" not in sync_columns:
                conn.execute("ALTER TABLE action_external_sync ADD COLUMN payload_fingerprint TEXT")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _record(row: sqlite3.Row) -> dict:
        return dict(row)

    def register(self, meeting_id: str, action_node_id: str, event_id: str,
                 decision_node_id: str | None = None) -> dict:
        if not meeting_id or not action_node_id or not event_id:
            raise ValueError("meeting_id, action_node_id and event_id are required")
        now = self._now()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM actions WHERE meeting_id=? AND event_id=?",
                (meeting_id, event_id)).fetchone()
            if existing:
                if existing["action_node_id"] != action_node_id or existing["decision_node_id"] != decision_node_id:
                    raise ActionConflict("event_id already used for another action")
                return self._record(existing)
            existing_node = conn.execute(
                "SELECT * FROM actions WHERE meeting_id=? AND action_node_id=?",
                (meeting_id, action_node_id)).fetchone()
            if existing_node:
                if existing_node["decision_node_id"] != decision_node_id:
                    raise ActionConflict("action already linked to another decision")
                return self._record(existing_node)
            action_id = f"act_{uuid.uuid4().hex[:16]}"
            try:
                conn.execute(
                    "INSERT INTO actions (id,meeting_id,action_node_id,decision_node_id,event_id,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (action_id, meeting_id, action_node_id, decision_node_id, event_id, now, now))
            except sqlite3.IntegrityError:
                raced = conn.execute(
                    "SELECT * FROM actions WHERE meeting_id=? AND (event_id=? OR action_node_id=?)",
                    (meeting_id, event_id, action_node_id)).fetchone()
                if raced and raced["action_node_id"] == action_node_id and raced["decision_node_id"] == decision_node_id:
                    return self._record(raced)
                raise ActionConflict("event_id already used for another action") from None
            action = self._record(conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone())
            self._append_event(conn, meeting_id, action_id, "registered", None, action, now)
            return action

    @staticmethod
    def _append_event(conn: sqlite3.Connection, meeting_id: str, action_id: str,
                      event_type: str, before: dict | None, after: dict, now: str) -> None:
        conn.execute(
            "INSERT INTO action_events (meeting_id, action_id, event_type, before_json, after_json, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (meeting_id, action_id, event_type, json.dumps(before, ensure_ascii=False) if before else None,
             json.dumps(after, ensure_ascii=False), now))

    def history(self, meeting_id: str, action_id: str | None = None) -> list[dict]:
        query = "SELECT * FROM action_events WHERE meeting_id=?"
        params: tuple[str, ...] = (meeting_id,)
        if action_id is not None:
            query += " AND action_id=?"
            params += (action_id,)
        query += " ORDER BY id"
        with self._connect() as conn:
            return [{"id": row["id"], "meeting_id": row["meeting_id"],
                     "action_id": row["action_id"], "event_type": row["event_type"],
                     "before": json.loads(row["before_json"]) if row["before_json"] else None,
                     "after": json.loads(row["after_json"]), "created_at": row["created_at"]}
                    for row in conn.execute(query, params)]

    def list_for_meeting(self, meeting_id: str) -> list[dict]:
        with self._connect() as conn:
            return [self._record(row) for row in conn.execute(
                "SELECT a.*, s.synced_version AS external_sync_version, "
                "s.payload_fingerprint AS external_sync_fingerprint, "
                "s.attempted_at AS external_sync_attempted_at, s.error_code AS external_sync_error "
                "FROM actions a LEFT JOIN action_external_sync s "
                "ON s.meeting_id=a.meeting_id AND s.action_id=a.id "
                "WHERE a.meeting_id=? ORDER BY a.created_at,a.id", (meeting_id,))]

    def record_external_sync(self, meeting_id: str, action_versions: dict[str, int],
                             error_code: str | None = None, *,
                             payload_fingerprints: dict[str, str] | None = None) -> None:
        if not meeting_id or not isinstance(action_versions, dict):
            raise ValueError("meeting_id and action_versions are required")
        if payload_fingerprints is not None and (
                not isinstance(payload_fingerprints, dict)
                or any(action_id not in action_versions or not isinstance(value, str) or not value
                       for action_id, value in payload_fingerprints.items())):
            raise ValueError("payload_fingerprints must map synced action ids to non-empty strings")
        now = self._now()
        with self._connect() as conn:
            for action_id, raw_version in action_versions.items():
                version = int(raw_version)
                exists = conn.execute(
                    "SELECT 1 FROM actions WHERE meeting_id=? AND id=?", (meeting_id, action_id),
                ).fetchone()
                if not exists:
                    raise KeyError("action not found")
                current = conn.execute(
                    "SELECT synced_version,payload_fingerprint FROM action_external_sync "
                    "WHERE meeting_id=? AND action_id=?",
                    (meeting_id, action_id),
                ).fetchone()
                succeeded = error_code is None
                synced_version = version if succeeded else (current["synced_version"] if current else None)
                fingerprint = ((payload_fingerprints or {}).get(action_id)
                               if succeeded else None)
                if fingerprint is None and current:
                    fingerprint = current["payload_fingerprint"]
                conn.execute(
                    "INSERT INTO action_external_sync "
                    "(meeting_id,action_id,synced_version,payload_fingerprint,attempted_at,error_code) "
                    "VALUES (?,?,?,?,?,?) ON CONFLICT(meeting_id,action_id) DO UPDATE SET "
                    "synced_version=excluded.synced_version, attempted_at=excluded.attempted_at, "
                    "payload_fingerprint=excluded.payload_fingerprint, error_code=excluded.error_code",
                    (meeting_id, action_id, synced_version, fingerprint, now, error_code),
                )

    def update(self, meeting_id: str, action_id: str, expected_version: int,
               changes: dict) -> dict:
        allowed = {"owner", "due_date", "status"}
        if not isinstance(changes, dict) or not changes or set(changes) - allowed:
            raise ValueError("invalid action changes")
        if "owner" in changes and (changes["owner"] is not None and
                                   (not isinstance(changes["owner"], str) or len(changes["owner"]) > 120)):
            raise ValueError("invalid owner")
        if "due_date" in changes and changes["due_date"] is not None:
            try:
                date.fromisoformat(changes["due_date"])
            except (TypeError, ValueError) as exc:
                raise ValueError("due_date must be YYYY-MM-DD") from exc
        if "status" in changes and changes["status"] not in {
            "open", "in_progress", "done", "delayed", "cancelled"
        }:
            raise ValueError("invalid action status")
        now = self._now()
        columns = list(changes)
        setters = ", ".join(f"{name}=?" for name in columns)
        with self._connect() as conn:
            previous = conn.execute(
                "SELECT * FROM actions WHERE id=? AND meeting_id=?",
                (action_id, meeting_id)).fetchone()
            cursor = conn.execute(
                f"UPDATE actions SET {setters}, version=version+1, updated_at=? "
                "WHERE id=? AND meeting_id=? AND version=?",
                (*[changes[name] for name in columns], now, action_id, meeting_id, expected_version))
            if cursor.rowcount != 1:
                existing = conn.execute(
                    "SELECT version FROM actions WHERE id=? AND meeting_id=?",
                    (action_id, meeting_id)).fetchone()
                if existing is None:
                    raise KeyError("action not found")
                raise ActionConflict(f"action is at version {existing['version']}")
            action = self._record(conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone())
            self._append_event(conn, meeting_id, action_id, "updated", self._record(previous), action, now)
            return action
