import tempfile
import asyncio
import unittest

from app.models import (GraphOp, GraphUpdateOp, InsightRecord, MeetingSummary,
                        NormUtterance, SpeakerRef)
from app.llm import MockLLM
from app.orchestrator import BoardAgent
from app.storage import StoreA, StoreB, VersionConflict


def board():
    return [
        {"id": "n_issue_root", "shape": "amo-node", "data": {
            "type": "issue", "label": "议题", "parent_id": None,
            "metadata_refs": [], "importance": {"level": "normal"}}},
        {"id": "n_point_a", "shape": "amo-node", "data": {
            "type": "point", "label": "旧观点", "parent_id": "n_issue_root",
            "metadata_refs": [], "importance": {"level": "normal"}}},
    ]


class NodeEditApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StoreA(self.tmp.name)
        self.store._schemas["meeting"] = "amo.board/v2"
        self.store.save("meeting", board())
        self.store.flush("meeting", force=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_patch_checks_version_records_field_changes_and_sets_manual_overrides(self):
        current = self.store.version("meeting")
        cells = self.store.patch_node("meeting", "n_point_a", {
            "label": "人工确认观点", "importance": "high", "status": "confirmed",
        }, expected_version=current, actor="leo", reason="会议主持人确认")
        data = next(cell["data"] for cell in cells if cell["id"] == "n_point_a")

        self.assertEqual(self.store.version("meeting"), current + 1)
        self.assertEqual(data["label"], "人工确认观点")
        self.assertEqual(data["importance"]["level"], "high")
        self.assertEqual(data["status"], "confirmed")
        self.assertEqual(data["manual_override"], {
            "label": True, "type": False, "parent_id": False,
            "importance": True, "status": True,
        })
        edit = self.store.history("meeting")[-1]["change"]
        self.assertEqual(edit["actor"], "leo")
        self.assertEqual(edit["reason"], "会议主持人确认")
        self.assertEqual(edit["fields"]["label"], {"before": "旧观点", "after": "人工确认观点"})

    def test_stale_patch_returns_latest_version_and_does_not_change_board(self):
        initial_version = self.store.version("meeting")
        self.store.patch_node("meeting", "n_point_a", {"label": "最新文本"},
                              expected_version=initial_version, actor="leo", reason="校对")

        with self.assertRaises(VersionConflict) as caught:
            self.store.patch_node("meeting", "n_point_a", {"label": "过期草稿"},
                                  expected_version=initial_version, actor="leo", reason="冲突测试")

        self.assertEqual(caught.exception.current_version, initial_version + 1)
        latest = next(c for c in caught.exception.cells if c["id"] == "n_point_a")
        self.assertEqual(latest["data"]["label"], "最新文本")
        self.assertEqual(self.store.version("meeting"), initial_version + 1)

    def test_parent_cycle_is_rejected_without_partial_write(self):
        version = self.store.version("meeting")
        with self.assertRaises(ValueError):
            self.store.patch_node("meeting", "n_issue_root", {"parent_id": "n_point_a"},
                                  expected_version=version, actor="leo", reason="无效移动")
        self.assertEqual(self.store.version("meeting"), version)

    def test_ai_replace_respects_manual_fields_but_keeps_other_updates_and_evidence(self):
        version = self.store.version("meeting")
        self.store.patch_node("meeting", "n_point_a", {"label": "人工文本"},
                              expected_version=version, actor="leo", reason="人工改写")

        cells, receipt = self.store.commit_graph_update("meeting", GraphUpdateOp(operations=[
            GraphOp(op="replace", node="n_point_a", label="AI覆盖文本", node_type="evidence", meta_ids=["m_new"]),
        ]))

        node = next(c for c in cells if c["id"] == "n_point_a")
        self.assertTrue(receipt["ok"])
        self.assertEqual(node["data"]["label"], "人工文本")
        self.assertEqual(node["data"]["type"], "evidence")
        self.assertIn("m_new", node["data"]["metadata_refs"])

    def test_delete_requires_explicit_child_action_and_reparents_children(self):
        version = self.store.version("meeting")
        with self.assertRaises(ValueError):
            self.store.apply_user_operation("meeting", "n_issue_root", "remove", {})
        self.assertEqual(self.store.version("meeting"), version)
        cells = self.store.apply_user_operation("meeting", "n_issue_root", "remove",
                                                {"children_action": "reparent"})
        child = next(c for c in cells if c["id"] == "n_point_a")
        self.assertIsNone(child["data"]["parent_id"])

    def test_undo_delete_restores_recursive_subtree_and_edges_without_losing_later_nodes(self):
        cells = self.store.load("meeting")
        cells.extend([
            {"id": "n_child", "shape": "amo-node", "data": {
                "type": "point", "label": "子节点", "parent_id": "n_point_a",
                "metadata_refs": ["m_child"], "importance": {"level": "normal"}}},
            {"id": "e_child", "shape": "edge", "source": {"cell": "n_point_a"},
             "target": {"cell": "n_child"}, "data": {"relation": "support"}},
        ])
        self.store.save("meeting", cells)
        deleted = self.store.apply_user_operation("meeting", "n_point_a", "remove",
            {"children_action": "delete"}, actor="leo")
        operation_version = self.store.version("meeting")
        self.assertNotIn("n_child", {cell["id"] for cell in deleted})
        self.store.commit_graph_update("meeting", GraphUpdateOp(operations=[
            GraphOp(op="add_node", node="n_later", node_type="point", label="后来新增", parent="n_issue_root"),
        ]))
        before_undo = self.store.version("meeting")
        restored = self.store.undo_operation("meeting", operation_version,
            expected_version=before_undo, actor="leo")
        restored_ids = {cell["id"] for cell in restored}
        self.assertTrue({"n_point_a", "n_child", "e_child", "n_later"}.issubset(restored_ids))
        self.assertEqual(self.store.version("meeting"), before_undo + 1)

    def test_rollback_restores_node_and_advances_version(self):
        version = self.store.version("meeting")
        self.store.patch_node("meeting", "n_point_a", {"label": "新文本"},
                              expected_version=version, actor="leo", reason="校正")
        self.store.commit_graph_update("meeting", GraphUpdateOp(operations=[
            GraphOp(op="add_node", node="n_point_a", meta_ids=["m_late"]),
        ]))
        before_rollback_version = self.store.version("meeting")
        cells = self.store.rollback_cell("meeting", "n_point_a", version)
        restored = next(c for c in cells if c["id"] == "n_point_a")
        self.assertEqual(restored["data"]["label"], "旧观点")
        self.assertIn("m_late", restored["data"]["metadata_refs"])
        self.assertEqual(self.store.version("meeting"), before_rollback_version + 1)
        self.assertEqual(self.store.history("meeting")[-1]["change"]["operation"], "rollback")

    def test_ai_duplicate_merge_cannot_delete_manually_edited_duplicate(self):
        version = self.store.version("meeting")
        cells = self.store.load("meeting")
        cells.append({"id": "n_point_b", "shape": "amo-node", "data": {
            "type": "point", "label": "重复文本", "parent_id": "n_issue_root",
            "metadata_refs": ["m_2"], "importance": {"level": "normal"}}})
        self.store.save("meeting", cells)
        self.store.patch_node("meeting", "n_point_b", {"label": "主持人修订"},
                              expected_version=version + 1, actor="leo", reason="修订观点")
        before = self.store.version("meeting")
        updated, receipt = self.store.commit_graph_update("meeting", GraphUpdateOp(operations=[
            GraphOp(op="merge_as_duplicate", node="n_point_b", parent="n_point_a"),
        ]))
        self.assertTrue(receipt["ok"])
        self.assertIn("n_point_b", {cell["id"] for cell in updated})
        self.assertEqual(self.store.version("meeting"), before)

    def test_lock_and_unlock_user_operations_are_audited(self):
        self.store.apply_user_operation("meeting", "n_point_a", "lock", {"locked": True}, actor="leo")
        self.assertTrue(next(c for c in self.store.load("meeting") if c["id"] == "n_point_a")["data"]["lock"]["locked"])
        record = self.store.history("meeting")[-1]
        self.assertEqual(record["change"]["operation"], "lock")
        self.assertEqual(record["change"]["actor"], "leo")
        self.store.apply_user_operation("meeting", "n_point_a", "lock", {"locked": False}, actor="leo")
        self.assertFalse(next(c for c in self.store.load("meeting") if c["id"] == "n_point_a")["data"]["lock"]["locked"])

    def test_locked_node_position_survives_ai_relayout(self):
        cells = self.store.load("meeting")
        point = next(c for c in cells if c["id"] == "n_point_a")
        point["position"] = {"x": 777, "y": 888}
        self.store.save("meeting", cells)
        self.store.apply_user_operation("meeting", "n_point_a", "lock", {"locked": True}, actor="leo")
        result, _ = self.store.commit_graph_update("meeting", GraphUpdateOp(operations=[
            GraphOp(op="add_node", node="n_new", node_type="point", label="新增节点", parent="n_issue_root"),
        ]))
        self.assertEqual(next(c for c in result if c["id"] == "n_point_a")["position"], {"x": 777, "y": 888})

    def test_merge_preview_is_read_only_and_merge_audits_transfers(self):
        cells = self.store.load("meeting")
        cells.extend([
            {"id": "n_point_b", "shape": "amo-node", "data": {
                "type": "point", "label": "重复观点", "parent_id": "n_issue_root",
                "metadata_refs": ["m_2"], "importance": {"level": "normal"}}},
            {"id": "n_point_c", "shape": "amo-node", "data": {
                "type": "evidence", "label": "子论据", "parent_id": "n_point_b",
                "metadata_refs": ["m_3"], "importance": {"level": "normal"}}},
        ])
        cells.append({"id": "e_b_c", "shape": "edge", "source": {"cell": "n_point_b"},
                      "target": {"cell": "n_point_c"}, "data": {"relation": "support"}})
        self.store.save("meeting", cells)
        version = self.store.version("meeting")
        preview = self.store.preview_node_merge("meeting", "n_point_b", "n_point_a")
        self.assertEqual(preview["version"], version)
        self.assertEqual(preview["reparented_children"], ["n_point_c"])
        self.assertEqual(preview["transferred_metadata_refs"], ["m_2"])
        self.assertEqual(self.store.version("meeting"), version)

        merged, _ = self.store.merge_nodes("meeting", "n_point_b", "n_point_a",
            expected_version=version, actor="leo", reason="确认重复")
        index = {cell["id"]: cell for cell in merged}
        self.assertNotIn("n_point_b", index)
        self.assertEqual(index["n_point_c"]["data"]["parent_id"], "n_point_a")
        self.assertIn("m_2", index["n_point_a"]["data"]["metadata_refs"])
        self.assertEqual(self.store.history("meeting")[-1]["change"]["operation"], "merge_as_duplicate")

    def test_manual_merge_rejects_opposing_or_protected_nodes(self):
        cells = self.store.load("meeting")
        cells.append({"id": "n_point_b", "shape": "amo-node", "data": {
            "type": "point", "label": "不同观点", "parent_id": "n_issue_root",
            "metadata_refs": [], "importance": {"level": "normal"}}})
        cells.append({"id": "e_oppose", "shape": "edge", "source": {"cell": "n_point_a"},
                      "target": {"cell": "n_point_b"}, "data": {"relation": "oppose"}})
        self.store.save("meeting", cells)
        with self.assertRaises(ValueError):
            self.store.preview_node_merge("meeting", "n_point_b", "n_point_a")
        cells = self.store.load("meeting")
        next(c for c in cells if c["id"] == "e_oppose")
        cells = [c for c in cells if c["id"] != "e_oppose"]
        self.store.save("meeting", cells)
        version = self.store.version("meeting")
        self.store.patch_node("meeting", "n_point_b", {"label": "人工核实"},
                              expected_version=version, actor="leo", reason="人工更正")
        with self.assertRaises(ValueError):
            self.store.merge_nodes("meeting", "n_point_b", "n_point_a",
                expected_version=self.store.version("meeting"), actor="leo")

    def test_v1_parent_edit_rewrites_structural_edge_without_mixing_schemas(self):
        legacy = StoreA(self.tmp.name + "/legacy")
        legacy._schemas["legacy"] = "amo.board/v1"
        legacy.save("legacy", [
            {"id": "n_issue_root", "shape": "amo-node", "data": {"type": "issue", "label": "root"}},
            {"id": "parent", "shape": "amo-node", "data": {"type": "point", "label": "parent"}},
            {"id": "child", "shape": "amo-node", "data": {"type": "point", "label": "child"}},
            {"id": "e_old", "shape": "edge", "source": {"cell": "n_issue_root"},
             "target": {"cell": "child"}, "data": {"relation": "subordinate"}},
        ])
        legacy.flush("legacy", force=True)
        cells = legacy.patch_node("legacy", "child", {"parent_id": "parent"},
            expected_version=legacy.version("legacy"), actor="leo", reason="调整归属")
        self.assertNotIn("parent_id", next(c for c in cells if c["id"] == "child")["data"])
        parent_edge = next(c for c in cells if c.get("shape") == "edge")
        self.assertEqual(parent_edge["source"]["cell"], "parent")

    def test_v1_delete_reparents_structural_children_instead_of_orphaning_them(self):
        legacy = StoreA(self.tmp.name + "/legacy-delete")
        legacy._schemas["legacy"] = "amo.board/v1"
        legacy.save("legacy", [
            {"id": "n_issue_root", "shape": "amo-node", "data": {"type": "issue", "label": "root"}},
            {"id": "parent", "shape": "amo-node", "data": {"type": "point", "label": "parent"}},
            {"id": "child", "shape": "amo-node", "data": {"type": "point", "label": "child"}},
            {"id": "e_parent", "shape": "edge", "source": {"cell": "n_issue_root"},
             "target": {"cell": "parent"}, "data": {"relation": "subordinate"}},
            {"id": "e_child", "shape": "edge", "source": {"cell": "parent"},
             "target": {"cell": "child"}, "data": {"relation": "subordinate"}},
        ])
        legacy.flush("legacy", force=True)
        cells = legacy.apply_user_operation("legacy", "parent", "remove", {"children_action": "reparent"})
        self.assertNotIn("parent", {c["id"] for c in cells})
        child_edge = next(c for c in cells if c.get("id") == "e_child")
        self.assertEqual(child_edge["source"]["cell"], "n_issue_root")

    def test_manual_fields_survive_store_restart_and_later_ai_updates(self):
        version = self.store.version("meeting")
        self.store.patch_node("meeting", "n_point_a", {
            "label": "人工保留文本", "type": "evidence", "parent_id": None,
            "importance": "high", "status": "confirmed",
        }, expected_version=version, actor="leo", reason="最终确认")
        restarted = StoreA(self.tmp.name)
        restarted._schemas["meeting"] = "amo.board/v2"
        cells, _ = restarted.commit_graph_update("meeting", GraphUpdateOp(operations=[
            GraphOp(op="replace", node="n_point_a", label="AI新文本", node_type="point"),
            GraphOp(op="set_importance", node="n_point_a", importance="low"),
            GraphOp(op="move_node", node="n_point_a", parent="n_issue_root"),
        ]))
        node = next(c for c in cells if c["id"] == "n_point_a")
        self.assertEqual(node["data"]["label"], "人工保留文本")
        self.assertEqual(node["data"]["type"], "evidence")
        self.assertIsNone(node["data"]["parent_id"])
        self.assertEqual(node["data"]["importance"]["level"], "high")
        self.assertEqual(node["data"]["status"], "confirmed")

    def test_versioned_user_operation_rejects_stale_delete(self):
        version = self.store.version("meeting")
        self.store.apply_user_operation("meeting", "n_point_a", "edit_label",
            {"label": "人工文本"}, actor="leo", expected_version=version)
        with self.assertRaises(VersionConflict) as caught:
            self.store.apply_user_operation("meeting", "n_point_a", "remove", {},
                actor="leo", expected_version=version)
        self.assertEqual(caught.exception.current_version, version + 1)
        self.assertIn("n_point_a", {cell["id"] for cell in caught.exception.cells})

    def test_repeated_agent_ingestion_keeps_manual_fields_after_restart(self):
        version = self.store.version("meeting")
        self.store.patch_node("meeting", "n_point_a", {
            "label": "主持人确认的标签", "status": "confirmed",
        }, expected_version=version, actor="leo", reason="现场核实")

        class RepeatReplaceLLM(MockLLM):
            def analyze(self, filtered_text, filtered_meta_ids, meeting_title, messages=None):
                return MeetingSummary("meeting", meeting_title, [InsightRecord(
                    type="point", summary=filtered_text[0], evidence=filtered_meta_ids,
                    confidence=0.95,
                )])

            def sync(self, summary, board_cells, focus, messages=None, tool_executor=None):
                insight = summary.insights[0]
                new_type = "evidence" if insight.summary.endswith("一") else "action"
                return GraphUpdateOp(operations=[GraphOp(op="replace", node="n_point_a",
                    label=insight.summary, node_type=new_type, meta_ids=insight.evidence)])

        store_b = StoreB(self.tmp.name)
        agent = BoardAgent(self.store, store_b, RepeatReplaceLLM())
        for seq, text in enumerate(("AI建议一", "AI建议二"), start=1):
            utterance = NormUtterance(
                utterance_id=f"utt_{seq}", meeting_id="meeting", session_id=None, seq=seq,
                speaker=SpeakerRef("local:leo"), text=text, language="zh",
                start_offset_ms=seq * 100, end_offset_ms=seq * 100 + 80,
                received_at_ms=seq * 100, is_final=True, is_partial=False,
            )
            store_b.put(utterance.utterance_id, {"meta_id": utterance.utterance_id,
                "kind": "utt", "meeting_id": "meeting", "text": text,
                "speaker_ref": "local:leo"})
            result = asyncio.run(agent.run("meeting", [utterance], [], meeting_title="议题"))
            self.assertIsNone(result.get("error"))

        self.store.flush("meeting", force=True)
        store_b.flush(force=True)
        restarted = StoreA(self.tmp.name)
        restarted_store_b = StoreB(self.tmp.name)
        node = next(c for c in restarted.load("meeting") if c["id"] == "n_point_a")
        self.assertEqual(node["data"]["label"], "主持人确认的标签")
        self.assertEqual(node["data"]["type"], "action")
        self.assertEqual(node["data"]["status"], "confirmed")
        self.assertTrue({"utt_1", "utt_2"}.issubset(node["data"]["metadata_refs"]))
        self.assertEqual(restarted_store_b.get("utt_1")["text"], "AI建议一")
        self.assertEqual(restarted_store_b.get("utt_2")["text"], "AI建议二")


if __name__ == "__main__":
    unittest.main()
