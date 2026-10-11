import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import server
from app.action_ledger import ActionLedger
from app.storage import StoreA


class ActionRoutesTests(unittest.TestCase):
    def test_register_and_update_action_with_version_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StoreA(directory)
            store.create_meeting("mtg_one", "方案会")
            cells = store.load("mtg_one")
            cells.extend([
                {"id": "n_decision", "shape": "amo-node", "data": {"type": "conclusion", "label": "先试点", "status": "confirmed", "manual_override": {"status": True}}},
                {"id": "n_action", "shape": "amo-node", "data": {"type": "action", "label": "准备名单"}},
            ])
            store.save("mtg_one", cells)
            ledger = ActionLedger(Path(directory) / "actions.sqlite3")
            with patch.object(server, "store_a", store), patch.object(server, "action_ledger", ledger, create=True), TestClient(server.app) as client:
                body = {"action_node_id": "n_action", "decision_node_id": "n_decision", "event_id": "event-1"}
                created = client.post("/api/meetings/mtg_one/actions", json=body)
                self.assertEqual(created.status_code, 200, created.text)
                action = created.json()["action"]
                self.assertEqual(client.post("/api/meetings/mtg_one/actions", json=body).json()["action"]["id"], action["id"])
                updated = client.patch(f"/api/meetings/mtg_one/actions/{action['id']}", json={
                    "expected_version": action["version"],
                    "changes": {"owner": "小李", "due_date": "2026-10-20", "status": "in_progress"},
                })
                self.assertEqual(updated.status_code, 200, updated.text)
                self.assertEqual(updated.json()["action"]["owner"], "小李")
                stale = client.patch(f"/api/meetings/mtg_one/actions/{action['id']}", json={
                    "expected_version": action["version"], "changes": {"status": "done"},
                })
                self.assertEqual(stale.status_code, 409)
                listed = client.get("/api/meetings/mtg_one/actions")
                self.assertEqual(len(listed.json()["actions"]), 1)
                minutes = client.get("/api/meetings/mtg_one/minutes?format=markdown")
                self.assertIn("准备名单（负责人：小李", minutes.text)
                snapshot = client.get("/api/meetings/mtg_one/snapshot-file")
                self.assertEqual(snapshot.json()["actions"][0]["id"], action["id"])
                self.assertEqual(snapshot.json()["action_history"][-1]["event_type"], "updated")
                history = client.get(f"/api/meetings/mtg_one/actions/{action['id']}/history")
                self.assertEqual([event["event_type"] for event in history.json()["events"]],
                                 ["registered", "updated"])
                close = client.get("/api/meetings/mtg_one/close-preview")
                self.assertEqual(close.json()["review"]["incomplete_actions"], [])

    def test_rejects_link_to_unconfirmed_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StoreA(directory)
            store.create_meeting("mtg_one", "方案会")
            cells = store.load("mtg_one")
            cells.extend([
                {"id": "n_decision", "shape": "amo-node", "data": {"type": "conclusion", "label": "候选", "status": "confirmed"}},
                {"id": "n_action", "shape": "amo-node", "data": {"type": "action", "label": "准备名单"}},
            ])
            store.save("mtg_one", cells)
            with patch.object(server, "store_a", store), patch.object(server, "action_ledger", ActionLedger(Path(directory) / "actions.sqlite3")), TestClient(server.app) as client:
                response = client.post("/api/meetings/mtg_one/actions", json={
                    "action_node_id": "n_action", "decision_node_id": "n_decision", "event_id": "event-2",
                })
                self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
