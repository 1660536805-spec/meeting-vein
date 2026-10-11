import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import server
from app import meeting_links as meeting_links_module
from app.action_ledger import ActionLedger
from app.meeting_links import MeetingLinks, MeetingLinkConflict, topic_projection
from app.storage import StoreA, StoreB


class MeetingLinkTests(unittest.TestCase):
    def test_link_suggestion_route_filters_already_confirmed_associations(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StoreA(directory)
            store.create_meeting("mtg_old", "上次试点会", agenda=["远程办公试点"])
            store.create_meeting("mtg_new", "本次复盘会", agenda=["远程办公试点复盘"])
            old_topic = next(cell["id"] for cell in store.load("mtg_old")
                             if (cell.get("data") or {}).get("label") == "远程办公试点")
            new_topic = next(cell["id"] for cell in store.load("mtg_new")
                             if (cell.get("data") or {}).get("label") == "远程办公试点复盘")
            links = MeetingLinks(Path(directory) / "links.sqlite3")
            with patch.object(server, "store_a", store), patch.object(server, "meeting_links", links), \
                    TestClient(server.app) as client:
                response = client.get("/api/meetings/mtg_new/link-suggestions")
                self.assertEqual(response.status_code, 200, response.text)
                candidates = response.json()["suggestions"]
                self.assertEqual(candidates[0]["source_topic_node_id"], old_topic)
                self.assertEqual(candidates[0]["target_topic_node_id"], new_topic)
                self.assertTrue(candidates[0]["requires_confirmation"])
                links.create("mtg_old", "mtg_new", old_topic, new_topic, "manual-1", "主持人", "已确认")
                filtered = client.get("/api/meetings/mtg_new/link-suggestions")
        self.assertEqual(filtered.json()["suggestions"], [])

    def test_link_suggestion_route_matches_source_utterance_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StoreA(directory)
            store.create_meeting("mtg_old", "旧支持复盘")
            store.create_meeting("mtg_new", "新产品计划")
            old_cells = store.load("mtg_old")
            new_cells = store.load("mtg_new")
            old_cells.extend([
                {"id": "old_topic", "shape": "amo-node", "data": {"type": "issue", "label": "客户支持策略",
                    "parent_id": "n_issue_root"}},
                {"id": "old_point", "shape": "amo-node", "data": {"type": "point", "label": "一线响应安排",
                    "parent_id": "old_topic", "metadata_refs": ["old_quote"]}},
            ])
            new_cells.extend([
                {"id": "new_topic", "shape": "amo-node", "data": {"type": "issue", "label": "数据产品升级",
                    "parent_id": "n_issue_root"}},
                {"id": "new_point", "shape": "amo-node", "data": {"type": "point", "label": "页面体验修复",
                    "parent_id": "new_topic", "metadata_refs": ["new_quote"]}},
            ])
            store.save("mtg_old", old_cells)
            store.save("mtg_new", new_cells)
            metadata = StoreB(directory)
            metadata.put("old_quote", {"meta_id": "old_quote", "kind": "utt", "meeting_id": "mtg_old",
                "text": "每周五复核华东区客户工单，统计首次响应时间和问题解决率。"})
            metadata.put("new_quote", {"meta_id": "new_quote", "kind": "utt", "meeting_id": "mtg_new",
                "text": "复盘华东区客户工单的首次响应时间，跟进解决率。"})
            with patch.object(server, "store_a", store), patch.object(server, "store_b", metadata), \
                    patch.object(server, "meeting_links", MeetingLinks(Path(directory) / "links.sqlite3")), \
                    TestClient(server.app) as client:
                response = client.get("/api/meetings/mtg_new/link-suggestions")
                phrase_search = client.get("/api/meetings/mtg_new/link-suggestions?q=%E5%8D%8E%E4%B8%9C%E5%8C%BA")

        self.assertEqual(response.status_code, 200, response.text)
        suggestions = response.json()["suggestions"]
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["source_topic_node_id"], "old_topic")
        self.assertEqual(suggestions[0]["target_topic_node_id"], "new_topic")
        self.assertIn("华东", suggestions[0]["matching_context_terms"])
        self.assertEqual(len(phrase_search.json()["suggestions"]), 1)
        self.assertEqual(phrase_search.json()["suggestions"][0]["source_topic_node_id"], "old_topic")

    def test_link_suggestion_query_searches_beyond_the_default_top_twelve(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StoreA(directory)
            store.create_meeting("mtg_target", "新会")
            target_cells = store.load("mtg_target")
            target_cells.append({"id": "target_topic", "shape": "amo-node", "data": {
                "type": "issue", "label": "深圳客户服务策略", "parent_id": "n_issue_root"}})
            store.save("mtg_target", target_cells)
            for index in range(13):
                meeting_id = f"mtg_old_{index:02d}"
                store.create_meeting(meeting_id, f"历史会议 {index:02d}")
                cells = store.load(meeting_id)
                label = "华东客户服务策略" if index == 12 else "深圳客户服务策略"
                cells.append({"id": f"topic_{index}", "shape": "amo-node", "data": {
                    "type": "issue", "label": label, "parent_id": "n_issue_root"}})
                store.save(meeting_id, cells)
            links = MeetingLinks(Path(directory) / "links.sqlite3")
            with patch.object(server, "store_a", store), patch.object(server, "meeting_links", links), \
                    TestClient(server.app) as client:
                response = client.get("/api/meetings/mtg_target/link-suggestions?q=%E5%8D%8E%E4%B8%9C")

        self.assertEqual(response.status_code, 200, response.text)
        suggestions = response.json()["suggestions"]
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["source_meeting_id"], "mtg_old_12")

    def test_equal_score_suggestions_put_the_most_recent_meeting_first(self):
        target_cells = [
            {"id": "n_issue_root", "shape": "amo-node", "data": {"type": "issue", "label": "新会"}},
            {"id": "target_topic", "shape": "amo-node", "data": {"type": "issue", "label": "预算管理"}},
        ]
        source_cells = [
            {"id": "n_issue_root", "shape": "amo-node", "data": {"type": "issue", "label": "旧会"}},
            {"id": "source_topic", "shape": "amo-node", "data": {"type": "issue", "label": "预算管理"}},
        ]

        candidates = meeting_links_module.topic_link_suggestions(
            "mtg_target", target_cells,
            [
                {"meeting_id": "mtg_z_old", "title": "Z 旧会", "updated_at": "2026-01-01T00:00:00+00:00"},
                {"meeting_id": "mtg_a_new", "title": "A 新会", "updated_at": "2026-10-10T00:00:00+00:00"},
            ], lambda _meeting_id: source_cells,
        )

        self.assertEqual([item["source_meeting_id"] for item in candidates], ["mtg_a_new", "mtg_z_old"])
        self.assertEqual(candidates[0]["source_meeting_updated_at"], "2026-10-10T00:00:00+00:00")

    def test_suggestions_rank_recurrent_topics_but_only_return_candidates(self):
        old_cells = [
            {"id": "n_issue_root", "shape": "amo-node", "data": {"type": "issue", "label": "旧会"}},
            {"id": "old_topic", "shape": "amo-node", "data": {"type": "issue", "label": "远程办公试点"}},
            {"id": "old_action", "shape": "amo-node", "data": {"type": "action", "label": "无关行动"}},
        ]
        target_cells = [
            {"id": "n_issue_root", "shape": "amo-node", "data": {"type": "issue", "label": "新会"}},
            {"id": "target_topic", "shape": "amo-node", "data": {"type": "issue", "label": "远程办公试点复盘"}},
            {"id": "other_topic", "shape": "amo-node", "data": {"type": "issue", "label": "预算审批"}},
        ]
        suggest = getattr(meeting_links_module, "topic_link_suggestions", None)
        self.assertTrue(callable(suggest), "cross-meeting topic suggestions are not implemented")
        candidates = suggest(
            "mtg_new", target_cells,
            [{"meeting_id": "mtg_old", "title": "上次试点会"},
             {"meeting_id": "mtg_new", "title": "本次复盘会"}],
            lambda meeting_id: old_cells if meeting_id == "mtg_old" else target_cells,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source_topic_node_id"], "old_topic")
        self.assertEqual(candidates[0]["target_topic_node_id"], "target_topic")
        self.assertGreater(candidates[0]["score"], 0)
        self.assertTrue(candidates[0]["requires_confirmation"])
        self.assertTrue(candidates[0]["matching_terms"])

    def test_suggestions_can_match_different_topic_names_by_descendant_content(self):
        old_cells = [
            {"id": "n_issue_root", "shape": "amo-node", "data": {"type": "issue", "label": "旧会"}},
            {"id": "old_topic", "shape": "amo-node", "data": {"type": "issue", "label": "预算管理"}},
            {"id": "old_decision", "shape": "amo-node", "data": {"type": "conclusion", "label": "压降低差旅预算", "parent_id": "old_topic"}},
        ]
        target_cells = [
            {"id": "n_issue_root", "shape": "amo-node", "data": {"type": "issue", "label": "新会"}},
            {"id": "target_topic", "shape": "amo-node", "data": {"type": "issue", "label": "费用治理"}},
            {"id": "target_action", "shape": "amo-node", "data": {"type": "action", "label": "复核差旅预算并节约成本", "parent_id": "target_topic"}},
        ]
        candidates = meeting_links_module.topic_link_suggestions(
            "mtg_new", target_cells, [{"meeting_id": "mtg_old", "title": "上次预算会"}],
            lambda meeting_id: old_cells,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source_topic_node_id"], "old_topic")
        self.assertEqual(candidates[0]["target_topic_node_id"], "target_topic")
        self.assertIn("差旅", candidates[0]["matching_context_terms"])
        self.assertIn("预算", candidates[0]["matching_context_terms"])
        self.assertTrue(candidates[0]["requires_confirmation"])

    def test_suggestions_use_linked_utterances_when_topic_labels_and_nodes_are_different(self):
        old_cells = [
            {"id": "n_issue_root", "shape": "amo-node", "data": {"type": "issue", "label": "旧会"}},
            {"id": "old_topic", "shape": "amo-node", "data": {"type": "issue", "label": "客户支持策略"}},
            {"id": "old_point", "shape": "amo-node", "data": {"type": "point", "label": "一线响应安排",
                "parent_id": "old_topic", "metadata_refs": ["old_quote"]}},
        ]
        target_cells = [
            {"id": "n_issue_root", "shape": "amo-node", "data": {"type": "issue", "label": "新会"}},
            {"id": "target_topic", "shape": "amo-node", "data": {"type": "issue", "label": "数据产品升级"}},
            {"id": "target_point", "shape": "amo-node", "data": {"type": "point", "label": "页面体验修复",
                "parent_id": "target_topic", "metadata_refs": ["new_quote"]}},
        ]
        utterances = {
            "old_quote": {"meta_id": "old_quote", "kind": "utt",
                "text": "每周五复核华东区客户工单，统计首次响应时间和问题解决率。"},
            "new_quote": {"meta_id": "new_quote", "kind": "utt",
                "text": "复盘华东区客户工单的首次响应时间，跟进解决率。"},
        }

        candidates = meeting_links_module.topic_link_suggestions(
            "mtg_new", target_cells, [{"meeting_id": "mtg_old", "title": "旧会"}],
            lambda meeting_id: old_cells,
            load_metadata=lambda ids: [utterances[meta_id] for meta_id in ids if meta_id in utterances],
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source_topic_node_id"], "old_topic")
        self.assertEqual(candidates[0]["target_topic_node_id"], "target_topic")
        self.assertIn("华东", candidates[0]["matching_context_terms"])
        self.assertIn("客户", candidates[0]["matching_context_terms"])
        self.assertEqual(candidates[0]["source_evidence"][0]["text"], utterances["old_quote"]["text"])
        self.assertEqual(candidates[0]["target_evidence"][0]["text"], utterances["new_quote"]["text"])

    def test_suggestions_do_not_match_only_on_generic_topic_words(self):
        old_cells = [
            {"id": "n_issue_root", "shape": "amo-node", "data": {"type": "issue", "label": "旧会"}},
            {"id": "old_topic", "shape": "amo-node", "data": {"type": "issue", "label": "问题梳理"}},
        ]
        target_cells = [
            {"id": "n_issue_root", "shape": "amo-node", "data": {"type": "issue", "label": "新会"}},
            {"id": "target_topic", "shape": "amo-node", "data": {"type": "issue", "label": "问题响应"}},
        ]
        candidates = meeting_links_module.topic_link_suggestions(
            "mtg_new", target_cells, [{"meeting_id": "mtg_old", "title": "旧会"}],
            lambda meeting_id: old_cells,
        )

        self.assertEqual(candidates, [])

    def test_explicit_topic_link_is_idempotent_and_rejects_event_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            links = MeetingLinks(Path(directory) / "links.sqlite3")
            fields = ("mtg_old", "mtg_new", "n_old", "n_new", "evt-1", "主持人", "继续讨论试点")
            created = links.create(*fields)
            self.assertEqual(links.create(*fields)["id"], created["id"])
            self.assertEqual(MeetingLinks(Path(directory) / "links.sqlite3").list_for_target("mtg_new")[0]["id"], created["id"])
            with self.assertRaises(MeetingLinkConflict):
                links.create(*fields[:-1], "改变原因")

    def test_dismissed_suggestion_is_persisted_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "links.sqlite3"
            links = MeetingLinks(path)
            fields = ("mtg_old", "mtg_new", "n_old", "n_new", "dismiss-1", "主持人", "不是同一议题")

            dismissed = links.dismiss_suggestion(*fields)

            self.assertEqual(dismissed["source_topic_node_id"], "n_old")
            self.assertEqual(links.dismissed_for_target("mtg_new"), {("mtg_old", "n_old", "n_new")})
            self.assertEqual(MeetingLinks(path).dismissed_for_target("mtg_new"),
                             {("mtg_old", "n_old", "n_new")})
            self.assertEqual(links.dismiss_suggestion(*fields)["id"], dismissed["id"])
            with self.assertRaises(MeetingLinkConflict):
                links.dismiss_suggestion(*fields[:-1], "event id reused")

    def test_dismissal_endpoint_removes_suggestion_from_future_results(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StoreA(directory)
            store.create_meeting("mtg_old", "上次试点会", agenda=["远程办公试点"])
            store.create_meeting("mtg_new", "本次复盘会", agenda=["远程办公试点复盘"])
            old_topic = next(cell["id"] for cell in store.load("mtg_old")
                             if (cell.get("data") or {}).get("label") == "远程办公试点")
            new_topic = next(cell["id"] for cell in store.load("mtg_new")
                             if (cell.get("data") or {}).get("label") == "远程办公试点复盘")
            links = MeetingLinks(Path(directory) / "links.sqlite3")
            with patch.object(server, "store_a", store), patch.object(server, "meeting_links", links), \
                    TestClient(server.app) as client:
                self.assertTrue(client.get("/api/meetings/mtg_new/link-suggestions").json()["suggestions"])
                response = client.post("/api/meetings/mtg_new/link-dismissals", json={
                    "source_meeting_id": "mtg_old", "source_topic_node_id": old_topic,
                    "target_topic_node_id": new_topic, "event_id": "dismiss-1",
                    "actor": "主持人", "reason": "不是同一议题"})
                refreshed = client.get("/api/meetings/mtg_new/link-suggestions")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(refreshed.json()["suggestions"], [])

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
