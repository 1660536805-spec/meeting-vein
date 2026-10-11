import unittest
from types import SimpleNamespace

from scripts import generate_real_predictions


class RealPredictionProjectionTests(unittest.TestCase):
    def test_response_summary_keeps_hashes_and_shape_without_raw_content(self):
        message = SimpleNamespace(content='{"insights":[{"type":"point"}]}', tool_calls=[
            SimpleNamespace(function=SimpleNamespace(name="graph_update", arguments='{"secret":"example"}'))])
        summary = generate_real_predictions.summarize_model_message(message)
        self.assertEqual(summary["json_shape"], {"keys": ["insights"], "insight_count": 1})
        self.assertEqual(summary["tool_calls"][0]["name"], "graph_update")
        self.assertNotIn("secret", str(summary))
        self.assertNotIn("point", str(summary))

    def test_project_relation_edges_preserves_the_other_node_id(self):
        project = getattr(generate_real_predictions, "project_relation_edges", None)
        self.assertIsNotNone(project)
        cells = [
            {"id": "n1", "shape": "amo-node", "data": {"metadata_refs": ["utt_1"]}},
            {"id": "n2", "shape": "amo-node", "data": {"metadata_refs": ["utt_2"]}},
            {"id": "e1", "shape": "edge", "source": {"cell": "n1"},
             "target": {"cell": "n2"}, "data": {"relation": "oppose"}},
        ]
        self.assertEqual(project(cells, {1: "n1", 2: "n2"}), {
            1: [{"target_node_id": "n2", "relation": "oppose"}],
            2: [{"target_node_id": "n1", "relation": "oppose"}],
        })

    def test_failed_batches_are_omitted_from_committed_predictions(self):
        build = getattr(generate_real_predictions, "build_prediction_records", None)
        self.assertIsNotNone(build)
        records = build(
            {1: {"type": "issue"}, 2: {"type": "point"}},
            {2: "n2"}, {2: []}, {2},
        )
        self.assertEqual([record["line_no"] for record in records], [2])
        self.assertEqual(records[0]["relation_edges"], [])


if __name__ == "__main__":
    unittest.main()
