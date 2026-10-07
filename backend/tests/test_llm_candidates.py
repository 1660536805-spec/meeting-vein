import unittest

from app.llm import MockLLM
from app.models import InsightRecord, MeetingSummary, stable_hash


class MockCandidateTests(unittest.TestCase):
    def test_normalized_duplicate_candidates_reuse_id_and_accumulate_evidence(self):
        summary = MeetingSummary(meeting_id="m", insights=[
            InsightRecord(type="point", summary="团队需要更多异步协作。", evidence=["utt_1"]),
            InsightRecord(type="point", summary="团队需要更多异步协作！", evidence=["utt_2"]),
        ])
        operations = MockLLM().sync(summary, [{"id": "n_issue_root", "shape": "amo-node"}], []).operations
        adds = [op for op in operations if op.op == "add_node"]

        self.assertEqual(adds[0].node, adds[1].node)
        self.assertEqual(adds[0].meta_ids, ["utt_1"])
        self.assertEqual(adds[1].meta_ids, ["utt_2"])

    def test_normalized_duplicate_reuses_existing_legacy_mock_id(self):
        old_id = f"n_p_{stable_hash("团队需要更多异步协作。", 10 ** 6)}"
        summary = MeetingSummary(meeting_id="m", insights=[
            InsightRecord(type="point", summary="团队需要更多异步协作！", evidence=["utt_2"]),
        ])

        operations = MockLLM().sync(summary, [{"id": old_id, "shape": "amo-node",
                                                "data": {"type": "point", "label": "团队需要更多异步协作。"}}], []).operations

        self.assertEqual(next(op.node for op in operations if op.op == "add_node"), old_id)

    def test_similar_but_opposite_claims_keep_distinct_node_ids(self):
        summary = MeetingSummary(meeting_id="m", insights=[
            InsightRecord(type="point", summary="远程办公能提高效率。", evidence=["utt_1"]),
            InsightRecord(type="point", summary="远程办公不能提高效率。", evidence=["utt_2"]),
        ])

        operations = MockLLM().sync(summary, [{"id": "n_issue_root", "shape": "amo-node"}], []).operations
        adds = [op for op in operations if op.op == "add_node"]

        self.assertNotEqual(adds[0].node, adds[1].node)


if __name__ == "__main__":
    unittest.main()
