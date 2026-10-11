"""Standards-neutral, operator-configured task webhook delivery."""
from __future__ import annotations

import hashlib
import ipaddress
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class TaskSyncError(RuntimeError):
    """Safe-to-display task sync error with no remote response body or secret."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def validate_webhook_url(url: str) -> str:
    value = str(url or "").strip()
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError as exc:
        raise TaskSyncError("invalid_webhook_url") from exc
    if (not hostname or parsed.username or parsed.password or parsed.fragment
            or parsed.scheme not in {"https", "http"}):
        raise TaskSyncError("invalid_webhook_url")
    loopback = hostname == "localhost"
    try:
        loopback = loopback or ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        pass
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise TaskSyncError("https_required")
    if port is not None and not 1 <= port <= 65535:
        raise TaskSyncError("invalid_webhook_url")
    return value


def _stable_key(*parts: str) -> str:
    raw = "\0".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def action_sync_fingerprint(meeting_id: str, meeting_title: str,
                            action: dict, title: str) -> str:
    """Fingerprint every field sent for an action, including board-owned text."""
    content = {
        "meeting_id": meeting_id,
        "meeting_title": str(meeting_title or meeting_id),
        "id": str(action.get("id") or "").strip(),
        "action_node_id": str(action.get("action_node_id") or "").strip(),
        "decision_node_id": action.get("decision_node_id"),
        "title": str(title or "未命名待办"),
        "owner": action.get("owner"),
        "due_date": action.get("due_date"),
        "status": str(action.get("status") or "open"),
        "version": int(action.get("version") or 1),
    }
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def actions_needing_sync(meeting_id: str, meeting_title: str,
                         actions: list[dict], labels: dict[str, str]) -> tuple[list[dict], dict[str, str]]:
    """Return actions whose ledger version or outbound content changed since success."""
    pending = []
    fingerprints: dict[str, str] = {}
    for action in actions:
        action_id = str(action.get("id") or "").strip()
        action_node_id = str(action.get("action_node_id") or "").strip()
        title = str(labels.get(action_node_id) or "未命名待办")
        fingerprint = action_sync_fingerprint(meeting_id, meeting_title, action, title)
        fingerprints[action_id] = fingerprint
        if (action.get("external_sync_version") != action.get("version")
                or action.get("external_sync_fingerprint") != fingerprint):
            pending.append(action)
    return pending, fingerprints


def build_action_snapshot(meeting_id: str, meeting_title: str,
                          actions: list[dict], labels: dict[str, str]) -> dict:
    if not meeting_id:
        raise ValueError("meeting_id is required")
    items = []
    for action in actions:
        action_id = str(action.get("id") or "").strip()
        node_id = str(action.get("action_node_id") or "").strip()
        if not action_id or not node_id:
            raise ValueError("each action requires a stable id and action_node_id")
        version = int(action.get("version") or 1)
        title = str(labels.get(node_id) or "未命名待办")
        items.append({
            "id": action_id,
            "external_id": f"huimai:{meeting_id}:{action_id}",
            "idempotency_key": action_sync_fingerprint(meeting_id, meeting_title, action, title),
            "source_node_id": node_id,
            "decision_node_id": action.get("decision_node_id"),
            "title": title,
            "owner": action.get("owner"),
            "due_date": action.get("due_date"),
            "status": str(action.get("status") or "open"),
            "version": version,
        })
    return {
        "schema": "huimai.task-sync/v1",
        "operation": "upsert",
        "meeting": {"id": meeting_id, "title": str(meeting_title or meeting_id)},
        "actions": items,
    }


def deliver_action_snapshot(url: str, token: str, payload: dict, *,
                            timeout: float = 10, opener=None) -> int:
    endpoint = validate_webhook_url(url)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    batch_key = hashlib.sha256(body).hexdigest()
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
        "X-Huimai-Schema": str(payload.get("schema") or "huimai.task-sync/v1"),
        "Idempotency-Key": batch_key,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with (opener or urlopen)(request, timeout=max(0.1, min(float(timeout), 30))) as response:
            status_value = getattr(response, "status", None)
            status = int(status_value if status_value is not None else response.getcode())
    except HTTPError as exc:
        raise TaskSyncError(f"remote_http_{exc.code}") from None
    except (TimeoutError, URLError, OSError):
        raise TaskSyncError("remote_unavailable") from None
    if status < 200 or status >= 300:
        raise TaskSyncError(f"remote_http_{status}")
    return status
