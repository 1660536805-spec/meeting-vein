import unittest

from app.candidate_validation import annotate_graph_operations, validate_graph_update, validate_insights
from app.models import GraphOp, InsightRecord, NodeData, node_data_to_dict
from app.storage import StoreA


class CandidateValidationTests(unittest.TestCase):
    def setUp(self):
        self.board = [
            {"id": "n_issue_root", "shape": "amo-node", "data": {"type": "issue"}},
            {"id": "n_point_old", "shape": "amo-node", "data": {"type": "point"}},
        ]

    def test_rejects_unknown_operation_and_arbitrary_existing_reference(self):
        errors = validate_graph_update([
            {"op": "delete_everything", "node": "n_point_old"},
            {"op": "replace", "node": "invented_node", "label": "替换"},
        ], self.board, {"utt_1"})
        self.assertTrue(any("unknown operation" in error for error in errors))
        self.assertTrue(any("unknown node" in error for error in errors))

    def test_rejects_metadata_refs_not_from_current_analysis(self):
        errors = validate_graph_update([
            {"op": "add_node", "node": "n_point_new", "node_type": "point",
             "label": "新观点", "meta_ids": ["utt_other_meeting"]},
        ], self.board, {"utt_1"})
        self.assertTrue(any("untrusted metadata" in error for error in errors))

    def test_rejects_dangling_link_and_invalid_types(self):
        errors = validate_graph_update([
            {"op": "link", "source": "n_point_old", "target": "invented_node",
             "relation": "oppose"},
            {"op": "add_node", "node": "n_point_other", "node_type": "made_up",
             "label": "错误类型", "meta_ids": []},
        ], self.board, set())
        self.assertTrue(any("unknown node" in error for error in errors))
        self.assertTrue(any("invalid node type" in error for error in errors))

    def test_rejects_blank_or_unrenderably_long_candidate_labels(self):
        errors = validate_graph_update([
            {"op": "add_node", "node": "n_point_blank", "node_type": "point",
             "label": "   ", "meta_ids": []},
            {"op": "add_node", "node": "n_point_long", "node_type": "point",
             "label": "很长" * 21, "meta_ids": []},
        ], self.board, set())
        self.assertEqual(sum("invalid node label" in error for error in errors), 2)

    def test_accepts_generated_node_then_link_with_trusted_evidence(self):
        errors = validate_graph_update([
            {"op": "add_node", "node": "n_point_new", "node_type": "point",
             "label": "新观点", "meta_ids": ["utt_1"]},
            {"op": "link", "source": "n_issue_root", "target": "n_point_new",
             "relation": "subordinate"},
        ], self.board, {"utt_1"})
        self.assertEqual(errors, [])

    def test_rejects_new_ids_outside_server_generated_namespace(self):
        errors = validate_graph_update([
            {"op": "add_node", "node": "totally-free-form", "node_type": "point",
             "label": "新观点", "meta_ids": []},
        ], self.board, set())
        self.assertTrue(any("invalid generated node id" in error for error in errors))

    def test_mock_id_compatibility_is_opt_in_not_accepted_for_real_candidates(self):
        candidate = [{"op": "add_node", "node": "n_p_12345", "node_type": "point",
                      "label": "新观点", "meta_ids": []}]
        rejected = validate_graph_update(candidate, self.board, set())
        accepted = validate_graph_update(candidate, self.board, set(), allow_mock_ids=True)
        self.assertTrue(any("invalid generated node id" in error for error in rejected))
        self.assertEqual(accepted, [])

    def test_validates_candidate_types_confidence_evidence_and_local_ownership(self):
        candidates = [
            {"type": "question", "summary": "是否调整时间？", "confidence": 0.4,
             "evidence": ["utt_1"], "ownership_index": 1,
             "relation_to_related": "support"},
            {"type": "made_up", "summary": "", "confidence": 1.5,
             "evidence": ["utt_foreign"], "ownership_index": "n_issue_root",
             "relation_to_related": "invented"},
        ]
        errors = validate_insights(candidates, {"utt_1"})
        self.assertEqual(len(errors), 6)

    def test_conclusion_rewrite_must_preserve_old_node_and_add_replace_edge(self):
        self.board[1]["data"]["type"] = "conclusion"
        errors = validate_graph_update([
            {"op": "replace", "node": "n_point_old", "label": "推翻旧结论"},
        ], self.board, set())
        self.assertTrue(any("preserve conclusion" in error for error in errors))

    def test_opposing_nodes_cannot_be_merged_as_duplicates(self):
        self.board.append({"id": "oppose", "shape": "edge",
                           "source": {"cell": "n_issue_root"},
                           "target": {"cell": "n_point_old"},
                           "data": {"relation": "oppose"}})
        errors = validate_graph_update([
            {"op": "merge_as_duplicate", "node": "n_point_old", "parent": "n_issue_root"},
        ], self.board, set())
        self.assertTrue(any("opposing nodes cannot be merged" in error for error in errors))

    def test_low_confidence_and_importance_rationale_are_derived_from_evidence(self):
        op = GraphOp(op="add_node", node="n_point_new", node_type="point",
                     label="新观点", meta_ids=["utt_1"], confidence=0.99)
        candidate = InsightRecord(type="point", summary="新观点", evidence=["utt_1"],
                                  confidence=0.42, importance_hint="high",
                                  importance_rationale="affects_action")

        annotate_graph_operations([op], [candidate])

        self.assertEqual(op.confidence, 0.42)
        self.assertEqual(op.importance_rationale, "affects_action")
        data = node_data_to_dict(NodeData(type="point", label="新观点", confidence=op.confidence))
        self.assertTrue(data["needs_confirmation"])
        self.assertEqual(data["confidence"], 0.42)

    def test_store_records_low_confidence_and_preserves_superseded_conclusion(self):
        root = {"id": "n_issue_root", "shape": "amo-node", "data": {
            "type": "issue", "label": "主题", "parent_id": None}}
        old = {"id": "n_conclusion_old", "shape": "amo-node", "data": {
            "type": "conclusion", "label": "旧结论", "parent_id": "n_issue_root",
            "metadata_refs": ["utt_old"]}}
        index = {root["id"]: root, old["id"]: old}
        store = StoreA.__new__(StoreA)
        candidate = InsightRecord(type="conclusion", summary="新结论", evidence=["utt_1"],
                                  confidence=0.42, importance_rationale="adopted")
        add = GraphOp(op="add_node", node="n_conclusion_new", node_type="conclusion",
                      label="新结论", meta_ids=["utt_1"], confidence=0.99)
        annotate_graph_operations([add], [candidate])

        store._apply_one(index, add)
        store._apply_one(index, GraphOp(op="link", source="n_conclusion_old",
            target="n_conclusion_new", relation="replace"))

        self.assertEqual(index["n_conclusion_old"]["data"]["label"], "旧结论")
        self.assertEqual(index["n_conclusion_new"]["data"]["confidence"], 0.42)
        self.assertTrue(index["n_conclusion_new"]["data"]["needs_confirmation"])
        self.assertEqual(index["n_conclusion_new"]["data"]["importance"]["rationale"], "adopted")
        self.assertEqual(index["e_n_conclusion_old__n_conclusion_new"]["data"]["relation"], "replace")

    def test_repeated_candidate_appends_evidence_and_updates_mention_count(self):
        index = {"n_point_repeat": {"id": "n_point_repeat", "shape": "amo-node", "data": {
            "type": "point", "label": "同一观点", "mention_count": 1,
            "confidence": 0.4, "needs_confirmation": True, "metadata_refs": ["utt_0"],
            "importance": {"level": "normal"}}}}
        StoreA.__new__(StoreA)._apply_one(index, GraphOp(
            op="add_node", node="n_point_repeat", node_type="point", label="同一观点",
            meta_ids=["utt_1"], confidence=0.8, importance_rationale="repeated"))
        data = index["n_point_repeat"]["data"]
        self.assertEqual(data["mention_count"], 2)
        self.assertEqual(data["metadata_refs"], ["utt_0", "utt_1"])
        self.assertAlmostEqual(data["confidence"], 0.6)
        self.assertTrue(data["needs_confirmation"])
        self.assertEqual(data["importance"]["rationale"], "repeated")


if __name__ == "__main__":
    unittest.main()
