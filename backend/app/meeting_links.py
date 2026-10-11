"""Human-authorized links between topic nodes in two meetings."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


class MeetingLinkConflict(RuntimeError):
    pass


class MeetingLinks:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS meeting_links (
                id TEXT PRIMARY KEY, source_meeting_id TEXT NOT NULL,
                target_meeting_id TEXT NOT NULL, source_topic_node_id TEXT NOT NULL,
                target_topic_node_id TEXT NOT NULL, event_id TEXT NOT NULL,
                actor TEXT NOT NULL, reason TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(target_meeting_id,event_id),
                UNIQUE(source_meeting_id,target_meeting_id,source_topic_node_id,target_topic_node_id)
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS meeting_links_target ON meeting_links(target_meeting_id)")

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def create(self, source_meeting_id: str, target_meeting_id: str,
               source_topic_node_id: str, target_topic_node_id: str,
               event_id: str, actor: str, reason: str) -> dict:
        if source_meeting_id == target_meeting_id:
            raise ValueError("meetings must differ")
        if not all((source_meeting_id, target_meeting_id, source_topic_node_id,
                    target_topic_node_id, event_id, actor.strip(), reason.strip())):
            raise ValueError("link fields are required")
        if len(actor) > 120 or len(reason) > 500 or len(event_id) > 120:
            raise ValueError("link field too long")
        now = datetime.now(timezone.utc).isoformat()
        fields = (source_meeting_id, target_meeting_id, source_topic_node_id,
                  target_topic_node_id, event_id, actor.strip(), reason.strip())
        with self._connect() as conn:
            prior = conn.execute("SELECT * FROM meeting_links WHERE target_meeting_id=? AND event_id=?",
                                 (target_meeting_id, event_id)).fetchone()
            if prior:
                if tuple(prior[key] for key in ("source_meeting_id", "target_meeting_id",
                    "source_topic_node_id", "target_topic_node_id", "event_id", "actor", "reason")) != fields:
                    raise MeetingLinkConflict("event_id already used for another link")
                return dict(prior)
            link_id = f"lnk_{uuid.uuid4().hex[:16]}"
            try:
                conn.execute("INSERT INTO meeting_links VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (link_id, *fields, 1, now, now))
            except sqlite3.IntegrityError as exc:
                raise MeetingLinkConflict("topic link already exists") from exc
            return dict(conn.execute("SELECT * FROM meeting_links WHERE id=?", (link_id,)).fetchone())

    def list_for_target(self, meeting_id: str) -> list[dict]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM meeting_links WHERE target_meeting_id=? ORDER BY created_at,id",
                (meeting_id,))]


def topic_projection(cells: list[dict], topic_node_id: str) -> dict:
    """Scope a linked topic to its descendants without matching names across meetings."""
    nodes = {cell.get("id"): cell for cell in cells if cell.get("shape") != "edge"}
    topic = nodes.get(topic_node_id)
    if topic is None or (topic.get("data") or {}).get("type") != "issue":
        raise ValueError("topic node not found")
    parent = {node_id: (node.get("data") or {}).get("parent_id") for node_id, node in nodes.items()}
    for edge in cells:
        if edge.get("shape") != "edge" or (edge.get("data") or {}).get("relation") != "subordinate":
            continue
        source, target = edge.get("source"), edge.get("target")
        source_id = source.get("cell") if isinstance(source, dict) else source
        target_id = target.get("cell") if isinstance(target, dict) else target
        if target_id in nodes and not parent.get(target_id):
            parent[target_id] = source_id

    def belongs(node_id: str) -> bool:
        seen = set()
        while node_id and node_id not in seen:
            if node_id == topic_node_id:
                return True
            seen.add(node_id)
            node_id = parent.get(node_id)
        return False

    scoped = [node for node_id, node in nodes.items() if belongs(node_id)]
    confirmed = [node for node in scoped if (node.get("data") or {}).get("type") == "conclusion"
                 and (node.get("data") or {}).get("status") == "confirmed"
                 and ((node.get("data") or {}).get("manual_override") or {}).get("status") is True]
    evidence = [node for node in scoped if (node.get("data") or {}).get("metadata_refs")]
    def record(node: dict) -> dict:
        data = node.get("data") or {}
        return {"id": node.get("id"), "label": data.get("label") or node.get("id"),
                "metadata_refs": data.get("metadata_refs") or []}
    return {"topic": record(topic), "confirmed_decisions": [record(node) for node in confirmed],
            "evidence": [record(node) for node in evidence],
            "node_ids": [node.get("id") for node in scoped]}
