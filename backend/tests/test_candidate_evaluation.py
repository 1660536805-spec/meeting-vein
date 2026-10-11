import unittest

from app.candidate_evaluation import confusion_matrix, duplicate_pair_metrics, evaluation_report


class CandidateEvaluationTests(unittest.TestCase):
    def test_confusion_matrix_tracks_missed_and_misclassified_candidates(self):
        result = confusion_matrix(["point", "point", "question"],
                                  ["point", "conflict", "point"])
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["matrix"]["point"]["conflict"], 1)
        self.assertEqual(result["matrix"]["question"]["point"], 1)
        self.assertEqual(result["accuracy"], 1 / 3)

    def test_duplicate_metrics_use_pairs_instead_of_raw_item_counts(self):
        result = duplicate_pair_metrics(
            {1: "cluster-a", 2: "cluster-a", 3: "cluster-b", 4: "cluster-b"},
            {1: "node-a", 2: "node-a", 3: "node-c", 4: "node-d"},
        )
        self.assertEqual(result["true_positive_pairs"], 1)
        self.assertEqual(result["false_negative_pairs"], 1)
        self.assertEqual(result["precision"], 1.0)
        self.assertEqual(result["recall"], 0.5)
        self.assertEqual(result["f1"], 2 / 3)

    def test_report_includes_a_conflict_detection_confusion_matrix(self):
        rows = [
            {"line_no": "1", "gold_type": "point", "relation_anchor": "oppose:a", "relation_target_line_no": "2"},
            {"line_no": "2", "gold_type": "point", "relation_anchor": "support:b", "relation_target_line_no": "1"},
            {"line_no": "3", "gold_type": "point", "relation_anchor": "duplicate:c", "relation_target_line_no": "1"},
        ]
        report = evaluation_report(rows, {
            1: {"type": "point", "node_id": "n1", "relation_edges": [{"target_node_id": "n2", "relation": "support"}]},
            2: {"type": "point", "node_id": "n2", "relation_edges": [{"target_node_id": "n1", "relation": "support"}]},
            3: {"type": "point", "node_id": "n3", "relation_edges": [{"target_node_id": "n1", "relation": "oppose"}]},
        }, "mock")
        self.assertEqual(report["conflict_confusion"]["matrix"]["yes"]["no"], 1)
        self.assertEqual(report["conflict_confusion"]["matrix"]["no"]["yes"], 1)

    def test_relations_are_scored_against_the_annotated_target_node(self):
        rows = [
            {"line_no": "1", "gold_type": "point", "relation_anchor": ""},
            {"line_no": "2", "gold_type": "point", "relation_anchor": "oppose:first",
             "relation_target_line_no": "1"},
        ]
        report = evaluation_report(rows, {
            1: {"type": "point", "node_id": "node-first"},
            2: {"type": "point", "node_id": "node-second", "relation_edges": [
                {"target_node_id": "node-first", "relation": "oppose"},
                {"target_node_id": "node-third", "relation": "support"},
            ]},
        }, "real")
        self.assertEqual(report["relation_confusion"]["count"], 1)
        self.assertEqual(report["relation_confusion"]["correct"], 1)
        self.assertEqual(report["assignment"]["anchor_accuracy"], 1.0)

    def test_legacy_relation_guess_is_unscorable_without_target_edges(self):
        rows = [
            {"line_no": "1", "gold_type": "point", "relation_anchor": ""},
            {"line_no": "2", "gold_type": "point", "relation_anchor": "oppose:first",
             "relation_target_line_no": "1"},
        ]
        report = evaluation_report(rows, {
            1: {"type": "point", "node_id": "node-first"},
            2: {"type": "point", "node_id": "node-second", "relation": "oppose",
                "ownership_anchor": None},
        }, "real")
        self.assertEqual(report["relation_confusion"]["count"], 0)
        self.assertEqual(report["assignment"]["anchor_accuracy"], None)
        self.assertEqual(report["unscored_relation_rows"], 1)

    def test_duplicate_relation_uses_shared_node_instead_of_an_edge(self):
        rows = [
            {"line_no": "1", "gold_type": "point", "relation_anchor": ""},
            {"line_no": "2", "gold_type": "point", "relation_anchor": "duplicate:first",
             "relation_target_line_no": "1"},
        ]
        report = evaluation_report(rows, {
            1: {"type": "point", "node_id": "same"},
            2: {"type": "point", "node_id": "same", "relation_edges": []},
        }, "real")
        self.assertEqual(report["relation_confusion"]["correct"], 1)
        self.assertEqual(report["assignment"]["anchor_accuracy"], 1.0)


if __name__ == "__main__":
    unittest.main()
