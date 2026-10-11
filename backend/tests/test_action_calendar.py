import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import server
from app.action_ledger import ActionLedger
from app.storage import StoreA

try:
    from app.action_calendar import render_action_calendar
except ModuleNotFoundError:
    render_action_calendar = None


class ActionCalendarTests(unittest.TestCase):
    def render(self, *args, **kwargs):
        self.assertTrue(callable(render_action_calendar), "calendar export function is not implemented")
        return render_action_calendar(*args, **kwargs)

    def test_exports_open_due_actions_with_stable_identity_and_two_reminders(self):
        calendar = self.render(
            "产品路线评审",
            [
                {"id": "act_123", "action_node_id": "n_action_1", "owner": "小李",
                 "due_date": "2026-10-15", "status": "in_progress", "version": 3},
                {"id": "act_done", "action_node_id": "n_action_2", "owner": None,
                 "due_date": "2026-10-15", "status": "done", "version": 2},
                {"id": "act_no_due", "action_node_id": "n_action_3", "owner": None,
                 "due_date": None, "status": "open", "version": 1},
            ],
            {"n_action_1": "汇总评审意见"},
            now=datetime(2026, 10, 11, 1, 30, tzinfo=timezone.utc),
        )

        self.assertIn("UID:act_123@huimai.local", calendar)
        self.assertIn("ORGANIZER;CN=会脉:mailto:noreply@huimai.invalid", calendar)
        self.assertIn("SEQUENCE:3", calendar)
        self.assertIn("DTSTART:20261015T090000", calendar)
        self.assertIn("SUMMARY:[会脉行动] 汇总评审意见", calendar)
        self.assertIn("DESCRIPTION:会议：产品路线评审\\n负责人：小李", calendar)
        self.assertEqual(calendar.count("BEGIN:VEVENT"), 1)
        self.assertEqual(calendar.count("TRIGGER:"), 2)
        self.assertIn("TRIGGER:-P1D", calendar)
        self.assertIn("TRIGGER:PT0M", calendar)

    def test_global_event_uses_meeting_scoped_uid_and_title(self):
        calendar = self.render("会脉全部会议", [{
            "id": "act_123", "calendar_uid": "mtg_old-act_123",
            "calendar_label": "复盘指标", "calendar_meeting_title": "旧会",
            "owner": "小李", "due_date": "2026-10-15", "status": "open", "version": 1,
        }], {}, now=datetime(2026, 10, 11, tzinfo=timezone.utc))

        self.assertIn("UID:mtg_old-act_123@huimai.local", calendar)
        self.assertIn("SUMMARY:[会脉 · 旧会] 复盘指标", calendar)
        self.assertIn("DESCRIPTION:会议：旧会\\n负责人：小李", calendar)

    def test_overdue_subscription_repeats_daily_but_snapshot_only_reminds_once(self):
        now = datetime(2026, 10, 11, 0, 30, tzinfo=timezone.utc)
        action = {"id": "act_late", "action_node_id": "n_late", "owner": "小李",
                  "due_date": "2026-10-10", "status": "open", "version": 4}

        subscription = self.render("复盘会", [action], {"n_late": "提交复盘"},
                                   now=now, repeat_overdue=True)
        snapshot = self.render("复盘会", [action], {"n_late": "提交复盘"}, now=now)

        # The daily sequence starts with the next useful reminder, not at the
        # historic deadline (which would create years of stale recurrences).
        self.assertIn("DTSTART:20261011T090000", subscription)
        self.assertIn("RRULE:FREQ=DAILY", subscription)
        self.assertIn("SUMMARY:[逾期待办] 提交复盘", subscription)
        self.assertIn("TRIGGER:PT0M", subscription)
        self.assertEqual(subscription.count("TRIGGER:"), 1)
        self.assertIn("DTSTART:20261011T090000", snapshot)
        self.assertNotIn("RRULE:FREQ=DAILY", snapshot)
        self.assertIn("原截止日期：2026-10-10", snapshot)
        self.assertEqual(snapshot.count("TRIGGER:"), 1)

    def test_overdue_subscription_after_reminder_time_starts_tomorrow(self):
        now = datetime(2026, 10, 11, 2, 0, tzinfo=timezone.utc)
        calendar = self.render("复盘会", [{
            "id": "act_late", "action_node_id": "n_late", "owner": "小李",
            "due_date": "2026-10-10", "status": "open", "version": 4,
        }], {"n_late": "提交复盘"}, now=now, repeat_overdue=True)

        self.assertIn("DTSTART:20261012T090000", calendar)
        self.assertIn("RRULE:FREQ=DAILY", calendar)

    def test_escapes_calendar_text_and_folds_utf8_lines_without_splitting_characters(self):
        long_label = "版本,冻结;评审\\结果\n" + "跨会方案" * 20
        calendar = self.render(
            "季度评审",
            [{"id": "act_long", "action_node_id": "n_action_long", "owner": "小王",
              "due_date": "2026-10-15", "status": "open", "version": 1}],
            {"n_action_long": long_label},
            now=datetime(2026, 10, 11, tzinfo=timezone.utc),
        )
        self.assertIn("SUMMARY:[会脉行动] 版本\\,冻结\\;评审\\\\结果\\n", calendar)
        unfolded = calendar.replace("\r\n ", "")
        for line in calendar.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75)
        self.assertIn("跨会方案" * 10, unfolded)

    def test_meeting_calendar_route_returns_a_calendar_file(self):
        with TemporaryDirectory() as directory:
            store = StoreA(directory)
            store.create_meeting("mtg_calendar", "日历测试")
            cells = store.load("mtg_calendar")
            cells.append({"id": "n_action_calendar", "shape": "amo-node", "data": {
                "type": "action", "label": "准备演示"}})
            store.save("mtg_calendar", cells)
            ledger = ActionLedger(Path(directory) / "actions.sqlite3")
            action = ledger.register("mtg_calendar", "n_action_calendar", "evt-calendar")
            ledger.update("mtg_calendar", action["id"], action["version"], {
                "due_date": "2000-01-01", "owner": "小李"})
            with patch.object(server, "store_a", store), patch.object(server, "action_ledger", ledger), \
                    TestClient(server.app) as client:
                response = client.get("/api/meetings/mtg_calendar/actions.ics")
                snapshot = client.get("/api/meetings/mtg_calendar/actions.ics?snapshot=1")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("text/calendar", response.headers["content-type"])
        self.assertIn("SUMMARY:[逾期待办] 准备演示", response.text)
        self.assertIn("RRULE:FREQ=DAILY", response.text)
        self.assertEqual(snapshot.status_code, 200, snapshot.text)
        self.assertNotIn("RRULE:FREQ=DAILY", snapshot.text)

    def test_global_calendar_route_collects_open_due_actions_across_meetings(self):
        with TemporaryDirectory() as directory:
            store = StoreA(directory)
            ledger = ActionLedger(Path(directory) / "actions.sqlite3")
            for meeting_id, title, action_label in [
                ("mtg_old", "上次复盘", "整理试点结果"),
                ("mtg_new", "本次评审", "确认扩围条件"),
            ]:
                store.create_meeting(meeting_id, title)
                cells = store.load(meeting_id)
                cells.append({"id": "n_action_same", "shape": "amo-node", "data": {
                    "type": "action", "label": action_label}})
                store.save(meeting_id, cells)
                action = ledger.register(meeting_id, "n_action_same", f"evt-{meeting_id}")
                ledger.update(meeting_id, action["id"], action["version"], {
                    "due_date": "2000-01-01", "owner": "小李"})
            with patch.object(server, "store_a", store), patch.object(server, "action_ledger", ledger), \
                    TestClient(server.app) as client:
                response = client.get("/api/actions.ics")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("SUMMARY:[会脉 · 上次复盘 · 逾期] 整理试点结果", response.text)
        self.assertIn("SUMMARY:[会脉 · 本次评审 · 逾期] 确认扩围条件", response.text)
        self.assertIn("UID:mtg_old-act_", response.text)
        self.assertIn("UID:mtg_new-act_", response.text)
        self.assertEqual(response.text.count("BEGIN:VEVENT"), 2)
        self.assertEqual(response.text.count("RRULE:FREQ=DAILY"), 2)


if __name__ == "__main__":
    unittest.main()
