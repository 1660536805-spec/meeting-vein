import unittest

from app.meeting_links import topic_link_suggestions


class MeetingLinkContextCacheTests(unittest.TestCase):
    def test_shared_descendant_evidence_is_loaded_once_per_search(self):
        source_cells = [
            {"id": "n_issue_root", "shape": "amo-node", "data": {"type": "issue", "label": "旧会"}},
            {"id": "old_parent", "shape": "amo-node", "data": {
                "type": "issue", "label": "预算复盘", "parent_id": "n_issue_root"}},
            {"id": "old_child", "shape": "amo-node", "data": {
                "type": "issue", "label": "差旅预算", "parent_id": "old_parent"}},
            {"id": "old_point", "shape": "amo-node", "data": {
                "type": "point", "label": "复核差旅费用", "parent_id": "old_child",
                "metadata_refs": ["quote_shared"]}},
        ]
        target_cells = [
            {"id": "n_issue_root", "shape": "amo-node", "data": {"type": "issue", "label": "新会"}},
            {"id": "new_topic", "shape": "amo-node", "data": {
                "type": "issue", "label": "费用治理", "parent_id": "n_issue_root"}},
        ]
        loaded_ids = []

        def load_metadata(ids):
            loaded_ids.extend(ids)
            return [{"meta_id": "quote_shared", "kind": "utt", "text": "复核差旅费用并降低季度支出。"}]

        topic_link_suggestions(
            "mtg_new", target_cells, [{"meeting_id": "mtg_old", "title": "旧会"}],
            lambda _meeting_id: source_cells, load_metadata=load_metadata,
        )

        self.assertEqual(loaded_ids, ["quote_shared"])

    def test_search_finds_an_old_topic_by_its_own_evidence_without_a_topic_overlap(self):
        source_cells = [
            {"id": "n_issue_root", "shape": "amo-node", "data": {"type": "issue", "label": "旧会"}},
            {"id": "old_topic", "shape": "amo-node", "data": {
                "type": "issue", "label": "一线客服支持", "parent_id": "n_issue_root"}},
            {"id": "old_point", "shape": "amo-node", "data": {
                "type": "point", "label": "工单响应", "parent_id": "old_topic",
                "metadata_refs": ["old_quote"]}},
        ]
        target_cells = [
            {"id": "n_issue_root", "shape": "amo-node", "data": {"type": "issue", "label": "新会"}},
            {"id": "new_topic", "shape": "amo-node", "data": {
                "type": "issue", "label": "移动端产品规划", "parent_id": "n_issue_root"}},
            {"id": "new_point", "shape": "amo-node", "data": {
                "type": "point", "label": "上线夜间模式", "parent_id": "new_topic"}},
        ]

        suggestions = topic_link_suggestions(
            "mtg_new", target_cells,
            [{"meeting_id": "mtg_old", "title": "老客服会"}],
            lambda _meeting_id: source_cells,
            load_metadata=lambda ids: [{"meta_id": "old_quote", "kind": "utt",
                "text": "每周五复核华东区客户工单的首次响应时间。"} if item == "old_quote" else None
                for item in ids if item == "old_quote"],
            query="华东区",
        )

        self.assertEqual(len(suggestions), 1)
        self.assertTrue(suggestions[0]["search_only"])
        self.assertEqual(suggestions[0]["source_topic_node_id"], "old_topic")
        self.assertEqual(suggestions[0]["target_topic_node_id"], "")
        self.assertEqual(suggestions[0]["matched_search_terms"], ["华东区"])
        self.assertEqual(suggestions[0]["source_evidence"][0]["text"],
                         "每周五复核华东区客户工单的首次响应时间。")


if __name__ == "__main__":
    unittest.main()
