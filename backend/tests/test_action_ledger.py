import tempfile
import unittest
from pathlib import Path

from app.action_ledger import ActionConflict, ActionLedger


class ActionLedgerTests(unittest.TestCase):
    def test_registration_is_idempotent_and_persists_after_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "actions.sqlite3"
            ledger = ActionLedger(path)
            first = ledger.register("mtg_one", "n_action", "event-1", "n_decision")
            again = ledger.register("mtg_one", "n_action", "event-1", "n_decision")
            self.assertIn("id", first)
            self.assertEqual(first["id"], again["id"])
            self.assertEqual(len(ActionLedger(path).list_for_meeting("mtg_one")), 1)
            self.assertEqual(first["status"], "open")
            with self.assertRaises(ActionConflict):
                ledger.register("mtg_one", "n_action", "event-2", "different_decision")

    def test_stale_action_update_cannot_overwrite_human_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = ActionLedger(Path(directory) / "actions.sqlite3")
            action = ledger.register("mtg_one", "n_action", "event-1", "n_decision")
            self.assertIn("id", action)
            changed = ledger.update("mtg_one", action["id"], action["version"], {
                "owner": "小李", "due_date": "2026-10-20", "status": "in_progress",
            })
            self.assertEqual(changed["version"], action["version"] + 1)
            self.assertEqual(changed["owner"], "小李")
            with self.assertRaises(ActionConflict):
                ledger.update("mtg_one", action["id"], action["version"], {"status": "done"})
            self.assertEqual(ledger.list_for_meeting("mtg_one")[0]["status"], "in_progress")
            history = ledger.history("mtg_one", action["id"])
            self.assertEqual([item["event_type"] for item in history], ["registered", "updated"])
            self.assertEqual(history[-1]["before"]["owner"], None)
            self.assertEqual(history[-1]["after"]["owner"], "小李")


if __name__ == "__main__":
    unittest.main()
