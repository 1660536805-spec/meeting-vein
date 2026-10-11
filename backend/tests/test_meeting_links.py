import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import server
from app.action_ledger import ActionLedger
from app.meeting_links import MeetingLinks, MeetingLinkConflict, topic_projection
from app.storage import StoreA, StoreB


class MeetingLinkTests(unittest.TestCase):
    def test_explicit_topic_link_is_idempotent_and_rejects_event_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            links = MeetingLinks(Path(directory) / "links.sqlite3")
            fields = ("mtg_old", "mtg_new", "n_old", "n_new", "evt-1", "主持人", "继续讨论试点")
            created = links.create(*fields)
            self.assertEqual(links.create(*fields)["id"], created["id"])
            self.assertEqual(MeetingLinks(Path(directory) / "links.sqlite3").list_for_target("mtg_new")[0]["id"], created["id"])
            with self.assertRaises(MeetingLinkConflict):
                links.create(*fields[:-1], "改变原因")

    def test_topic_projection_uses_node_ancestry_and_human_confirmation(self):
        cells = [
            {"id": "topic", "shape": "amo-node", "data": {"type": "issue", "label": "试点"}},
            {"id": "decision", "shape": "amo-node", "data": {"type": "conclusion", "label": "先小范围",
                "parent_id": "topic", "status": "confirmed", "manual_override": {"status": True}}},
            {"id": "ai", "shape": "amo-node", "data": {"type": "conclusion", "label": "全量",
                "parent_id": "topic", "status": "confirmed"}},
            {"id": "other", "shape": "amo-node", "data": {"type": "conclusion", "label": "不相关",
                "status": "confirmed", "manual_override": {"status": True}}},
        ]
        projection = topic_projection(cells, "topic")
        self.assertEqual([item["id"] for item in projection["confirmed_decisions"]], ["decision"])
        with self.assertRaises(ValueError):
            topic_projection(cells, "decision")

    def test_timeline_returns_old_decision_new_evidence_and_unfinished_action(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StoreA(directory)
            store.create_meeting("mtg_old", "旧会", agenda=["试点"])
            store.create_meeting("mtg_new", "新会", agenda=["试点"])
            for meeting_id, decision_id, label in [("mtg_old", "d_old", "先试点"),
                                                    ("mtg_new", "d_new", "继续试点")]:
                cells = store.load(meeting_id)
                topic = next(c for c in cells if (c.get("data") or {}).get("label") == "试点")
                cells.append({"id": decision_id, "shape": "amo-node", "data": {
                    "type": "conclusion", "label": label, "parent_id": topic["id"],
                    "status": "confirmed", "manual_override": {"status": True}}})
                if meeting_id == "mtg_old":
                    cells.append({"id": "a_old", "shape": "amo-node", "data": {
                        "type": "action", "label": "复盘", "parent_id": topic["id"]}})
                else:
                    cells.append({"id": "e_new", "shape": "amo-node", "data": {
                        "type": "viewpoint", "label": "新增反馈", "parent_id": topic["id"],
                        "metadata_refs": ["missing_ref"]}})
                store.save(meeting_id, cells)
            links = MeetingLinks(Path(directory) / "links.sqlite3")
            actions = ActionLedger(Path(directory) / "actions.sqlite3")
            actions.register("mtg_old", "a_old", "action-1", "d_old")
            old_topic = next(c["id"] for c in store.load("mtg_old") if (c.get("data") or {}).get("label") == "试点")
            new_topic = next(c["id"] for c in store.load("mtg_new") if (c.get("data") or {}).get("label") == "试点")
            with patch.object(server, "store_a", store), patch.object(server, "store_b", StoreB(directory)), \
                    patch.object(server, "meeting_links", links), patch.object(server, "action_ledger", actions), \
                    TestClient(server.app) as client:
                body = {"source_meeting_id": "mtg_old", "source_topic_node_id": old_topic,
                        "target_topic_node_id": new_topic, "event_id": "link-1",
                        "actor": "主持人", "reason": "复盘试点反馈"}
                self.assertEqual(client.post("/api/meetings/mtg_new/links", json=body).status_code, 200)
                self.assertEqual(client.post("/api/meetings/mtg_new/links", json=body).status_code, 200)
                timeline = client.get("/api/meetings/mtg_new/timeline")
                self.assertEqual(timeline.status_code, 200, timeline.text)
                entry = timeline.json()["entries"][0]
                self.assertEqual(entry["old_topic"]["confirmed_decisions"][0]["label"], "先试点")
                self.assertEqual(entry["new_topic"]["confirmed_decisions"][0]["label"], "继续试点")
                self.assertEqual(entry["unfinished_actions"][0]["action_node_id"], "a_old")
                self.assertEqual(entry["new_topic"]["evidence"][0]["source_utterances"][0]["text"], None)
                invalid = {**body, "event_id": "link-2", "source_topic_node_id": "not_a_topic"}
                self.assertEqual(client.post("/api/meetings/mtg_new/links", json=invalid).status_code, 422)


if __name__ == "__main__":
    unittest.main()
