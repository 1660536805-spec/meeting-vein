import unittest
import tempfile

from app.minutes import parse_agenda, render_minutes
from app.storage import StoreA


class MinutesTests(unittest.TestCase):
    def test_parse_agenda_keeps_order_and_removes_list_markers(self):
        self.assertEqual(
            parse_agenda("# 议程\n1. 现状\n- 风险评估\n\n2、下一步"),
            ["现状", "风险评估", "下一步"],
        )

    def test_minutes_only_call_confirmed_nodes_decisions_and_show_provenance_gaps(self):
        cells = [
            {"id": "issue", "data": {"type": "issue", "label": "上线节奏"}},
            {"id": "decision", "data": {"type": "conclusion", "label": "先小范围试点", "status": "confirmed", "manual_override": {"status": True}, "parent_id": "issue", "metadata_refs": ["u1"]}},
            {"id": "draft", "data": {"type": "conclusion", "label": "下月全量上线", "status": "needs_confirmation", "parent_id": "issue"}},
            {"id": "todo", "data": {"type": "todo", "label": "整理试点名单", "parent_id": "issue", "status": "open"}},
        ]
        utterances = [{"meta_id": "u1", "text": "我们先选两个客户试点。", "speaker_ref": "张三"}]
        markdown = render_minutes("产品评审", ["上线节奏"], "ended", cells, utterances)
        self.assertIn("先小范围试点", markdown)
        self.assertIn("张三：我们先选两个客户试点。", markdown)
        self.assertNotIn("### 已确认结论\n- **下月全量上线**", markdown)
        self.assertIn("### 未决与分歧\n- 下月全量上线（未确认）", markdown)
        self.assertIn("待补充负责人", markdown)
        self.assertIn("待补充期限", markdown)
        self.assertIn("未确认", markdown)

    def test_minutes_use_action_ledger_and_never_promote_ai_status(self):
        cells = [
            {"id": "issue", "data": {"type": "issue", "label": "上线节奏"}},
            {"id": "ai_decision", "data": {"type": "conclusion", "label": "直接发布", "status": "confirmed", "parent_id": "issue"}},
            {"id": "human_decision", "data": {"type": "conclusion", "label": "先试点", "status": "confirmed", "manual_override": {"status": True}, "parent_id": "issue"}},
            {"id": "action", "data": {"type": "action", "label": "整理名单", "parent_id": "issue"}},
        ]
        actions = [{"action_node_id": "action", "decision_node_id": "human_decision", "owner": "小李",
                    "due_date": "2026-10-20", "status": "in_progress"}]
        markdown = render_minutes("评审", ["上线节奏"], "ended", cells, [], actions=actions)
        self.assertIn("**先试点**", markdown)
        self.assertNotIn("**直接发布**", markdown)
        self.assertIn("直接发布（未确认）", markdown)
        self.assertIn("整理名单（负责人：小李；期限：2026-10-20", markdown)
        self.assertIn("来源决议：先试点", markdown)

    def test_minutes_include_manual_change_reason(self):
        markdown = render_minutes("评审", [], "ended", [], [], history=[{
            "version": 4, "change": {"source": "manual", "actor": "主持人", "reason": "会后核对",
                                       "fields": {"label": {"before": "旧文案", "after": "新文案"}}},
        }])
        self.assertIn("主持人", markdown)
        self.assertIn("会后核对", markdown)

    def test_html_export_escapes_meeting_content(self):
        html = render_minutes("<script>x</script>", [], "ended", [], [], format="html")
        self.assertNotIn("<script>x</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_agenda_and_lifecycle_survive_store_restart(self):
        with tempfile.TemporaryDirectory() as root:
            store = StoreA(root)
            store.create_meeting("mtg_minutes", "评审会", agenda=["现状", "下一步"])
            store.set_meeting_status("mtg_minutes", "live")
            store.set_meeting_status("mtg_minutes", "ended")
            lifecycle = [record for record in store.history("mtg_minutes")
                         if (record.get("change") or {}).get("source") == "lifecycle"]
            self.assertTrue(lifecycle)
            self.assertEqual(lifecycle[-1]["meeting_status"], "ended")
            store.save_snapshot("mtg_minutes")
            self.assertEqual(store.history("mtg_minutes")[-1]["agenda"], ["现状", "下一步"])
            self.assertEqual(store.history("mtg_minutes")[-1]["meeting_status"], "ended")
            reopened = StoreA(root)
            meeting = next(row for row in reopened.list_meetings() if row["meeting_id"] == "mtg_minutes")
            self.assertEqual(meeting["status"], "ended")
            self.assertEqual(meeting["agenda"], ["现状", "下一步"])
            reopened.set_meeting_status("mtg_minutes", "live")
            self.assertEqual(StoreA(root).list_meetings()[0]["status"], "live")


if __name__ == "__main__":
    unittest.main()
