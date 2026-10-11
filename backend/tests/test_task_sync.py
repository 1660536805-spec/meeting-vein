from __future__ import annotations

import json
import importlib
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.action_ledger import ActionLedger
from app import server
from app.storage import StoreA
from fastapi.testclient import TestClient

try:
    task_sync = importlib.import_module("app.task_sync")
except ImportError:
    task_sync = None


class TaskWebhookTests(unittest.TestCase):
    def service(self):
        self.assertIsNotNone(task_sync, "task webhook integration is not implemented")
        return task_sync

    def test_action_snapshot_is_stable_upsert_payload_without_transcript_text(self):
        service = self.service()
        actions = [{
            "id": "act_123", "action_node_id": "node_action", "decision_node_id": "node_decision",
            "owner": "小李", "due_date": "2026-10-20", "status": "open", "version": 3,
        }]
        first = service.build_action_snapshot("mtg_demo", "产品评审", actions,
                                              {"node_action": "整理评审结论"})
        retry = service.build_action_snapshot("mtg_demo", "产品评审", actions,
                                              {"node_action": "整理评审结论"})
        changed = service.build_action_snapshot("mtg_demo", "产品评审", [
            {**actions[0], "version": 4, "status": "done"}
        ], {"node_action": "整理评审结论"})

        self.assertEqual(first["schema"], "huimai.task-sync/v1")
        self.assertEqual(first["operation"], "upsert")
        self.assertEqual(first["actions"][0]["external_id"], "huimai:mtg_demo:act_123")
        self.assertEqual(first["actions"][0]["idempotency_key"], retry["actions"][0]["idempotency_key"])
        self.assertNotEqual(first["actions"][0]["idempotency_key"], changed["actions"][0]["idempotency_key"])
        self.assertEqual(first["actions"][0]["title"], "整理评审结论")
        self.assertNotIn("transcript", json.dumps(first, ensure_ascii=False).lower())

    def test_webhook_requires_https_except_for_loopback_development_endpoints(self):
        service = self.service()
        self.assertEqual(service.validate_webhook_url("https://tasks.example.test/hooks/123"),
                         "https://tasks.example.test/hooks/123")
        self.assertEqual(service.validate_webhook_url("http://127.0.0.1:5678/webhook"),
                         "http://127.0.0.1:5678/webhook")
        with self.assertRaises(service.TaskSyncError):
            service.validate_webhook_url("http://tasks.example.test/hooks/123")

    def test_delivery_sends_json_and_optional_bearer_secret(self):
        service = self.service()
        observed = {}

        class Response:
            status = 202

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        def opener(request, timeout):
            observed["url"] = request.full_url
            observed["auth"] = request.get_header("Authorization")
            observed["content_type"] = request.get_header("Content-type")
            observed["payload"] = json.loads(request.data)
            observed["timeout"] = timeout
            return Response()

        payload = service.build_action_snapshot("mtg_demo", "产品评审", [], {})
        status = service.deliver_action_snapshot(
            "https://tasks.example.test/hooks/123", "secret", payload, timeout=7, opener=opener,
        )

        self.assertEqual(status, 202)
        self.assertEqual(observed["auth"], "Bearer secret")
        self.assertEqual(observed["content_type"], "application/json; charset=utf-8")
        self.assertEqual(observed["payload"], payload)
        self.assertEqual(observed["timeout"], 7)

    def test_action_ledger_keeps_external_sync_version_and_retry_error(self):
        with TemporaryDirectory() as directory:
            ledger = ActionLedger(Path(directory) / "actions.sqlite3")
            action = ledger.register("mtg_demo", "node_action", "evt-action")
            self.assertTrue(hasattr(ledger, "record_external_sync"),
                            "action ledger does not persist external sync state")
            ledger.record_external_sync("mtg_demo", {action["id"]: action["version"]})
            synced = ledger.list_for_meeting("mtg_demo")[0]
            self.assertEqual(synced["external_sync_version"], action["version"])
            self.assertIsNone(synced["external_sync_error"])

            updated = ledger.update("mtg_demo", action["id"], action["version"], {"owner": "小李"})
            ledger.record_external_sync("mtg_demo", {action["id"]: updated["version"]},
                                        error_code="remote_unavailable")
            failed = ledger.list_for_meeting("mtg_demo")[0]
            self.assertEqual(failed["external_sync_version"], action["version"])
            self.assertEqual(failed["external_sync_error"], "remote_unavailable")

            ledger.record_external_sync("mtg_demo", {action["id"]: updated["version"]})
            retried = ledger.list_for_meeting("mtg_demo")[0]
            self.assertEqual(retried["external_sync_version"], updated["version"])
            self.assertIsNone(retried["external_sync_error"])

    def test_sync_route_sends_action_snapshot_and_returns_persisted_sync_state(self):
        with TemporaryDirectory() as directory:
            store = StoreA(directory)
            store.create_meeting("mtg_task_sync", "产品评审")
            cells = store.load("mtg_task_sync")
            cells.append({"id": "node_action", "shape": "amo-node", "data": {
                "type": "action", "label": "整理评审结论"}})
            store.save("mtg_task_sync", cells)
            ledger = ActionLedger(Path(directory) / "actions.sqlite3")
            action = ledger.register("mtg_task_sync", "node_action", "evt-action")
            action = ledger.update("mtg_task_sync", action["id"], action["version"], {
                "owner": "小李", "due_date": "2026-10-20",
            })
            observed = {}

            def deliver(url, token, payload, *, timeout):
                observed.update({"url": url, "token": token, "payload": payload, "timeout": timeout})
                return 202

            config = SimpleNamespace(api_token="", task_webhook_url="https://tasks.example.test/hook",
                                     task_webhook_token="secret", task_webhook_timeout_s=7)
            with patch.object(server, "store_a", store), patch.object(server, "action_ledger", ledger), \
                    patch.object(server.config, "CONFIG", config), \
                    patch.object(server, "deliver_action_snapshot", deliver, create=True):
                client = TestClient(server.app)
                response = client.post("/api/meetings/mtg_task_sync/actions/sync")
                listed = client.get("/api/meetings/mtg_task_sync/actions")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(observed["token"], "secret")
        self.assertEqual(observed["timeout"], 7)
        self.assertEqual(observed["payload"]["actions"][0]["title"], "整理评审结论")
        self.assertEqual(response.json()["synced_count"], 1)
        self.assertEqual(listed.json()["actions"][0]["external_sync_version"], action["version"])

    def test_sync_route_resends_board_title_edits_without_duplicate_unchanged_pushes(self):
        with TemporaryDirectory() as directory:
            store = StoreA(directory)
            store.create_meeting("mtg_task_title", "产品评审")
            cells = store.load("mtg_task_title")
            cells.append({"id": "node_action", "shape": "amo-node", "data": {
                "type": "action", "label": "整理评审结论"}})
            store.save("mtg_task_title", cells)
            ledger = ActionLedger(Path(directory) / "actions.sqlite3")
            ledger.register("mtg_task_title", "node_action", "evt-action")
            config = SimpleNamespace(api_token="", task_webhook_url="https://tasks.example.test/hook",
                                     task_webhook_token="", task_webhook_timeout_s=7)
            deliveries = []

            def deliver(_url, _token, payload, *, timeout):
                deliveries.append(payload)
                return 202

            with patch.object(server, "store_a", store), patch.object(server, "action_ledger", ledger), \
                    patch.object(server.config, "CONFIG", config), \
                    patch.object(server, "deliver_action_snapshot", deliver, create=True), \
                    TestClient(server.app) as client:
                first = client.post("/api/meetings/mtg_task_title/actions/sync")
                unchanged = client.post("/api/meetings/mtg_task_title/actions/sync")
                edited_cells = store.load("mtg_task_title")
                next(cell for cell in edited_cells if cell.get("id") == "node_action")["data"]["label"] = "整理修订后的评审结论"
                store.save("mtg_task_title", edited_cells)
                pending_state = client.get("/api/meetings/mtg_task_title/actions")
                edited = client.post("/api/meetings/mtg_task_title/actions/sync")

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(unchanged.status_code, 200, unchanged.text)
        self.assertEqual(unchanged.json()["sync_state"], "up_to_date")
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(edited.json()["sync_state"], "sent")
        self.assertTrue(pending_state.json()["actions"][0]["external_sync_pending"])
        self.assertFalse(edited.json()["actions"][0]["external_sync_pending"])
        self.assertNotIn("external_sync_fingerprint", edited.json()["actions"][0])
        self.assertEqual(len(deliveries), 2)
        self.assertEqual(deliveries[0]["actions"][0]["title"], "整理评审结论")
        self.assertEqual(deliveries[1]["actions"][0]["title"], "整理修订后的评审结论")
        self.assertNotEqual(deliveries[0]["actions"][0]["idempotency_key"],
                            deliveries[1]["actions"][0]["idempotency_key"])

    def test_sync_route_reports_unconfigured_connector_without_sending(self):
        with TemporaryDirectory() as directory:
            store = StoreA(directory)
            store.create_meeting("mtg_task_sync", "产品评审")
            ledger = ActionLedger(Path(directory) / "actions.sqlite3")
            config = SimpleNamespace(api_token="", task_webhook_url="", task_webhook_token="",
                                     task_webhook_timeout_s=7)
            with patch.object(server, "store_a", store), patch.object(server, "action_ledger", ledger), \
                    patch.object(server.config, "CONFIG", config):
                response = TestClient(server.app).post("/api/meetings/mtg_task_sync/actions/sync")

        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("task_sync_not_configured", response.text)


if __name__ == "__main__":
    unittest.main()
