"""Human-authorized links between topic nodes in two meetings."""
from __future__ import annotations

import sqlite3
import uuid
import re
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
            conn.execute("""CREATE TABLE IF NOT EXISTS meeting_link_dismissals (
                id TEXT PRIMARY KEY, source_meeting_id TEXT NOT NULL,
                target_meeting_id TEXT NOT NULL, source_topic_node_id TEXT NOT NULL,
                target_topic_node_id TEXT NOT NULL, event_id TEXT NOT NULL,
                actor TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(target_meeting_id,event_id),
                UNIQUE(source_meeting_id,target_meeting_id,source_topic_node_id,target_topic_node_id)
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS meeting_link_dismissals_target ON meeting_link_dismissals(target_meeting_id)")

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

    def dismiss_suggestion(self, source_meeting_id: str, target_meeting_id: str,
                           source_topic_node_id: str, target_topic_node_id: str,
                           event_id: str, actor: str, reason: str) -> dict:
        if source_meeting_id == target_meeting_id:
            raise ValueError("meetings must differ")
        if not all((source_meeting_id, target_meeting_id, source_topic_node_id,
                    target_topic_node_id, event_id, actor.strip(), reason.strip())):
            raise ValueError("dismissal fields are required")
        if len(actor) > 120 or len(reason) > 500 or len(event_id) > 120:
            raise ValueError("dismissal field too long")
        fields = (source_meeting_id, target_meeting_id, source_topic_node_id,
                  target_topic_node_id, event_id, actor.strip(), reason.strip())
        with self._connect() as conn:
            prior_event = conn.execute(
                "SELECT * FROM meeting_link_dismissals WHERE target_meeting_id=? AND event_id=?",
                (target_meeting_id, event_id)).fetchone()
            if prior_event:
                if tuple(prior_event[key] for key in (
                    "source_meeting_id", "target_meeting_id", "source_topic_node_id",
                    "target_topic_node_id", "event_id", "actor", "reason")) != fields:
                    raise MeetingLinkConflict("event_id already used for another dismissal")
                return dict(prior_event)
            pair = conn.execute(
                "SELECT * FROM meeting_link_dismissals WHERE source_meeting_id=? AND target_meeting_id=? "
                "AND source_topic_node_id=? AND target_topic_node_id=?", fields[:4]).fetchone()
            if pair:
                return dict(pair)
            dismissal_id = f"dsm_{uuid.uuid4().hex[:16]}"
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("INSERT INTO meeting_link_dismissals VALUES (?,?,?,?,?,?,?,?,?)",
                         (dismissal_id, *fields, now))
            return dict(conn.execute("SELECT * FROM meeting_link_dismissals WHERE id=?",
                                     (dismissal_id,)).fetchone())

    def dismissed_for_target(self, meeting_id: str) -> set[tuple[str, str, str]]:
        with self._connect() as conn:
            return {(row["source_meeting_id"], row["source_topic_node_id"], row["target_topic_node_id"])
                    for row in conn.execute(
                        "SELECT source_meeting_id,source_topic_node_id,target_topic_node_id "
                        "FROM meeting_link_dismissals WHERE target_meeting_id=?", (meeting_id,))}


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


_GENERIC_TOPIC_TERMS = {"会议", "讨论", "议题", "主题", "问题"}


def _topic_features(value: object) -> set[str]:
    """Build language-neutral lexical features, including Chinese bigrams."""
    features: set[str] = set()
    for token in re.findall(r"[\u3400-\u9fff]+|[a-z0-9]+", str(value or "").casefold()):
        if re.fullmatch(r"[\u3400-\u9fff]+", token):
            if token in _GENERIC_TOPIC_TERMS:
                continue
            if len(token) == 1:
                features.add(token)
            else:
                features.update(token[index:index + 2] for index in range(len(token) - 1)
                                if token[index:index + 2] not in _GENERIC_TOPIC_TERMS)
        else:
            features.add(token)
    return features


def _topic_context(cells: list[dict], topic_id: str, load_metadata=None,
                   metadata_cache: dict[str, dict] | None = None) -> tuple[set[str], list[dict]]:
    """Collect searchable features and linked utterance excerpts from a topic subtree."""
    scoped_ids = set(topic_projection(cells, topic_id)["node_ids"])
    features: set[str] = set()
    metadata_ids: list[str] = []
    metadata_node_labels: dict[str, str] = {}
    for cell in cells or []:
        if cell.get("id") not in scoped_ids or cell.get("shape") == "edge":
            continue
        data = cell.get("data") or {}
        features.update(_topic_features(data.get("label")))
        features.update(_topic_features(data.get("description")))
        label = str(data.get("label") or cell.get("id") or "")
        for ref in data.get("metadata_refs") or []:
            if isinstance(ref, str) and ref:
                metadata_ids.append(ref)
                metadata_node_labels.setdefault(ref, label)
    evidence: list[dict] = []
    if load_metadata and metadata_ids:
        cache = metadata_cache if metadata_cache is not None else {}
        unique_ids = list(dict.fromkeys(metadata_ids))
        uncached_ids = [meta_id for meta_id in unique_ids if meta_id not in cache]
        if uncached_ids:
            try:
                records = load_metadata(uncached_ids) or []
            except Exception:
                records = []  # Keep lexical suggestions available if evidence storage is temporarily unavailable.
            for record in records:
                meta_id = record.get("meta_id")
                if isinstance(meta_id, str) and meta_id:
                    cache[meta_id] = record
        records = [cache[meta_id] for meta_id in unique_ids if meta_id in cache]
        for record in records:
            text = record.get("text")
            if record.get("kind") == "summary" or not isinstance(text, str) or not text.strip():
                continue
            text = text.strip()
            features.update(_topic_features(text))
            evidence.append({
                "meta_id": str(record.get("meta_id") or ""),
                "node_label": metadata_node_labels.get(str(record.get("meta_id") or ""), "相关节点"),
                "speaker": str(record.get("display_name") or record.get("speaker_ref") or "未知发言人"),
                "text": text if len(text) <= 240 else text[:237].rstrip() + "…",
            })
    return features, evidence


def _matching_context_evidence(records: list[dict], matching_terms: set[str], limit: int = 2) -> list[dict]:
    ranked = []
    for record in records:
        shared = _topic_features(record.get("text")) & matching_terms
        if shared:
            ranked.append((len(shared), record))
    ranked.sort(key=lambda item: (-item[0], item[1]["meta_id"], item[1]["text"]))
    return [record for _score, record in ranked[:max(0, limit)]]


def topic_link_suggestions(target_meeting_id: str, target_cells: list[dict],
                           meeting_summaries: list[dict], load_cells,
                           *, limit: int = 12, load_metadata=None,
                           query: str = "") -> list[dict]:
    """Rank lexical topic matches for human review; never create links here."""
    target_topics = [cell for cell in target_cells or []
                     if cell.get("shape") != "edge"
                     and (cell.get("data") or {}).get("type") == "issue"
                     and cell.get("id") != "n_issue_root"
                     and str((cell.get("data") or {}).get("label") or "").strip()]
    target_features = {cell["id"]: _topic_features((cell.get("data") or {}).get("label"))
                       for cell in target_topics}
    metadata_cache: dict[str, dict] = {}
    target_contexts = {cell["id"]: _topic_context(target_cells, cell["id"], load_metadata, metadata_cache)
                       for cell in target_topics}
    target_by_id = {cell["id"]: cell for cell in target_topics}
    suggestions = []
    search_terms = [term.casefold() for term in str(query or "").split() if term.strip()]
    search_hits: list[dict] = []
    loaded: dict[str, list[dict]] = {}
    for summary in meeting_summaries or []:
        source_id = str(summary.get("meeting_id") or "")
        if not source_id or source_id == target_meeting_id:
            continue
        if source_id not in loaded:
            loaded[source_id] = load_cells(source_id) or []
        source_topics = [cell for cell in loaded[source_id]
                         if cell.get("shape") != "edge"
                         and (cell.get("data") or {}).get("type") == "issue"
                         and cell.get("id") != "n_issue_root"
                         and str((cell.get("data") or {}).get("label") or "").strip()]
        source_contexts = {cell["id"]: _topic_context(loaded[source_id], cell["id"],
                                                       load_metadata, metadata_cache)
                           for cell in source_topics}
        for source in source_topics:
            source_data = source.get("data") or {}
            source_label = str(source_data.get("label") or "").strip()
            source_features = _topic_features(source_label)
            source_context, source_evidence = source_contexts.get(source.get("id"), (set(), []))
            if search_terms:
                meeting_title = str(summary.get("title") or source_id)
                source_text = " ".join([meeting_title, source_label,
                    *(record["text"] for record in source_evidence)]).casefold()
                source_search_features = source_features | source_context
                matched_terms = [term for term in search_terms
                    if term in source_text or bool(_topic_features(term))
                    and _topic_features(term).issubset(source_search_features)]
                if len(matched_terms) == len(search_terms):
                    query_features = set().union(*(_topic_features(term) for term in matched_terms))
                    search_hits.append({
                        "source_meeting_id": source_id,
                        "source_meeting_title": meeting_title,
                        "source_meeting_updated_at": str(summary.get("updated_at") or ""),
                        "source_topic_node_id": source.get("id"),
                        "source_topic_label": source_label,
                        "target_topic_node_id": "",
                        "target_topic_label": "",
                        "score": 0.0,
                        "matching_terms": [],
                        "matching_context_terms": [],
                        "matched_search_terms": matched_terms,
                        "source_evidence": _matching_context_evidence(source_evidence, query_features),
                        "target_evidence": [],
                        "requires_confirmation": True,
                        "search_only": True,
                    })
            if not source_features and not source_context:
                continue
            for target_id, target in target_by_id.items():
                shared = source_features & target_features[target_id]
                source_scoped = source_context
                target_scoped, target_evidence = target_contexts.get(target_id, (set(), []))
                context_shared = source_scoped & target_scoped
                if not shared and not context_shared:
                    continue
                union = source_features | target_features[target_id]
                title_score = len(shared) / len(union) if union else 0.0
                context_denominator = len(source_scoped) + len(target_scoped)
                context_score = (2 * len(context_shared) / context_denominator
                                 if context_denominator else 0.0)
                # Preserve strong title matches while allowing evidence within
                # the topic subtree to recover topics whose names were rephrased.
                score = max(title_score, 0.4 * title_score + 0.6 * context_score)
                if score < 0.12:
                    continue
                target_label = str((target.get("data") or {}).get("label") or "").strip()
                suggestions.append({
                    "source_meeting_id": source_id,
                    "source_meeting_title": str(summary.get("title") or source_id),
                    "source_meeting_updated_at": str(summary.get("updated_at") or ""),
                    "source_topic_node_id": source.get("id"),
                    "source_topic_label": source_label,
                    "target_topic_node_id": target_id,
                    "target_topic_label": target_label,
                    "score": round(score, 3),
                    "matching_terms": sorted(shared),
                    "matching_context_terms": sorted(context_shared),
                    "source_evidence": _matching_context_evidence(source_evidence, context_shared),
                    "target_evidence": _matching_context_evidence(target_evidence, context_shared),
                    "requires_confirmation": True,
                })

    if search_terms:
        def searchable_text(item: dict) -> tuple[str, set[str]]:
            fields = (
                item["source_meeting_title"], item["source_topic_label"],
                item["target_topic_label"], *item["matching_terms"],
                *item["matching_context_terms"],
                *(record["text"] for record in item["source_evidence"]),
                *(record["text"] for record in item["target_evidence"]),
            )
            value = " ".join(str(field) for field in fields).casefold()
            features = set().union(*(_topic_features(field) for field in fields)) if fields else set()
            return value, features

        def matches_query(item: dict) -> bool:
            text, features = searchable_text(item)
            return all(term in text or bool(_topic_features(term))
                       and _topic_features(term).issubset(features) for term in search_terms)

        suggestions = [item for item in suggestions if matches_query(item)]
        suggested_topics = {(item["source_meeting_id"], item["source_topic_node_id"])
                            for item in suggestions}
        suggestions.extend(item for item in search_hits
                           if (item["source_meeting_id"], item["source_topic_node_id"])
                           not in suggested_topics)

    def recency(item: dict) -> float:
        try:
            value = datetime.fromisoformat(item["source_meeting_updated_at"].replace("Z", "+00:00"))
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.timestamp()
        except (TypeError, ValueError, OverflowError):
            return 0.0

    suggestions.sort(key=lambda item: (-item["score"], -recency(item),
                                       item["source_meeting_title"],
                                       item["source_topic_label"], item["target_topic_label"]))
    return suggestions[:max(0, min(int(limit), 100))]
