from __future__ import annotations

import tempfile
import sqlite3
import unittest
from pathlib import Path

from app.action_ledger import ActionLedger
from app.task_sync import action_sync_fingerprint, actions_needing_sync, build_action_snapshot


class ActionSyncFingerprintTests(unittest.TestCase):
    def action(self):
        return {
            "id": "act_1",
            "action_node_id": "node_action",
            "decision_node_id": "node_decision",
            "owner": "小李",
            "due_date": "2026-10-20",
            "status": "open",
            "version": 3,
            "external_sync_version": 3,
        }

    def test_changed_board_title_requires_sync_even_when_ledger_version_is_unchanged(self):
        action = self.action()
        old_title = "整理评审结论"
        action["external_sync_fingerprint"] = action_sync_fingerprint(
            "mtg_demo", "产品评审", action, old_title)

        unchanged, _ = actions_needing_sync("mtg_demo", "产品评审", [action], {"node_action": old_title})
        changed, fingerprints = actions_needing_sync("mtg_demo", "产品评审", [action], {
            "node_action": "整理修订后的评审结论",
        })

        self.assertEqual(unchanged, [])
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["id"], action["id"])
        self.assertNotEqual(fingerprints[action["id"]], action["external_sync_fingerprint"])

    def test_payload_idempotency_key_changes_with_title_but_is_stable_on_retry(self):
        action = self.action()
        first = build_action_snapshot("mtg_demo", "产品评审", [action], {
            "node_action": "整理评审结论",
        })
        retry = build_action_snapshot("mtg_demo", "产品评审", [action], {
            "node_action": "整理评审结论",
        })
        edited = build_action_snapshot("mtg_demo", "产品评审", [action], {
            "node_action": "整理修订后的评审结论",
        })

        self.assertEqual(first["actions"][0]["idempotency_key"], retry["actions"][0]["idempotency_key"])
        self.assertNotEqual(first["actions"][0]["idempotency_key"], edited["actions"][0]["idempotency_key"])

    def test_sync_fingerprint_persists_and_failed_attempt_does_not_advance_it(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = ActionLedger(Path(directory) / "actions.sqlite3")
            action = ledger.register("mtg_demo", "node_action", "event-1")
            fingerprint = "fingerprint-v1"
            ledger.record_external_sync("mtg_demo", {action["id"]: action["version"]},
                                        payload_fingerprints={action["id"]: fingerprint})
            synced = ledger.list_for_meeting("mtg_demo")[0]
            self.assertEqual(synced["external_sync_fingerprint"], fingerprint)

            updated = ledger.update("mtg_demo", action["id"], action["version"], {"owner": "小王"})
            ledger.record_external_sync("mtg_demo", {action["id"]: updated["version"]},
                                        error_code="remote_unavailable",
                                        payload_fingerprints={action["id"]: "fingerprint-v2"})
            failed = ledger.list_for_meeting("mtg_demo")[0]
            self.assertEqual(failed["external_sync_fingerprint"], fingerprint)
            self.assertEqual(failed["external_sync_error"], "remote_unavailable")

    def test_existing_sync_table_is_upgraded_without_losing_previous_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "actions.sqlite3"
            with sqlite3.connect(path) as conn:
                conn.execute("""CREATE TABLE action_external_sync (
                    meeting_id TEXT NOT NULL, action_id TEXT NOT NULL, synced_version INTEGER,
                    attempted_at TEXT NOT NULL, error_code TEXT,
                    PRIMARY KEY(meeting_id, action_id))""")
                conn.execute("""CREATE TABLE actions (
                    id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL, action_node_id TEXT NOT NULL,
                    decision_node_id TEXT, event_id TEXT NOT NULL, owner TEXT, due_date TEXT,
                    status TEXT NOT NULL DEFAULT 'open', version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
                conn.execute("INSERT INTO actions VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                    "act_existing", "mtg_demo", "node_action", None, "event-1", None, None,
                    "open", 2, "created", "updated"))
                conn.execute("INSERT INTO action_external_sync VALUES (?,?,?,?,?)", (
                    "mtg_demo", "act_existing", 2, "attempted", None))

            ledger = ActionLedger(path)
            upgraded = ledger.list_for_meeting("mtg_demo")[0]

            self.assertEqual(upgraded["external_sync_version"], 2)
            self.assertIsNone(upgraded["external_sync_fingerprint"])
            pending, _ = actions_needing_sync("mtg_demo", "产品评审", [upgraded], {
                "node_action": "原有标题",
            })
            self.assertEqual([action["id"] for action in pending], ["act_existing"])


if __name__ == "__main__":
    unittest.main()
