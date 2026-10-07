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
            {"line_no": "1", "gold_type": "point", "relation_anchor": "oppose:a"},
            {"line_no": "2", "gold_type": "point", "relation_anchor": "support:b"},
            {"line_no": "3", "gold_type": "point", "relation_anchor": "duplicate:c"},
        ]
        report = evaluation_report(rows, {
            1: {"type": "point", "relation": "support"},
            2: {"type": "point", "relation": "support"},
            3: {"type": "point", "relation": "oppose"},
        }, "mock")
        self.assertEqual(report["conflict_confusion"]["matrix"]["yes"]["no"], 1)
        self.assertEqual(report["conflict_confusion"]["matrix"]["no"]["yes"], 1)


if __name__ == "__main__":
    unittest.main()
