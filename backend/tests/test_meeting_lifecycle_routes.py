import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import server
from app.storage import StoreA, StoreB


class MeetingLifecycleRouteTests(unittest.TestCase):
    def test_create_review_end_reopen_and_export_minutes(self):
        with tempfile.TemporaryDirectory() as root:
            store = StoreA(root + "/boards")
            store_b = StoreB(root + "/utterances")
            with patch.object(server, "store_a", store), patch.object(server, "store_b", store_b), TestClient(server.app) as client:
                created = client.post("/api/meetings", json={
                    "title": "评审会", "agenda_text": "# 议程\n1. 上线节奏\n- 风险处理",
                })
                self.assertEqual(created.status_code, 200, created.text)
                meeting_id = created.json()["meeting_id"]
                self.assertEqual(created.json()["agenda"], ["上线节奏", "风险处理"])
                board = client.get(f"/api/board/{meeting_id}")
                self.assertIn("上线节奏", [cell.get("data", {}).get("label") for cell in board.json()["cells"]])
                review = client.get(f"/api/meetings/{meeting_id}/close-preview")
                self.assertEqual(review.status_code, 200, review.text)
                self.assertEqual(review.json()["review"]["unconfirmed"], [])
                ended = client.post(f"/api/meetings/{meeting_id}/status", json={"status": "ended"})
                self.assertEqual(ended.status_code, 200, ended.text)
                self.assertEqual(ended.json()["status"], "ended")
                blocked_input = client.post("/api/cli/push", json={
                    "meeting_id": meeting_id, "text": "结束后不能悄悄继续自动摄取",
                })
                self.assertEqual(blocked_input.status_code, 409)
                md = client.get(f"/api/meetings/{meeting_id}/minutes", params={"format": "markdown"})
                self.assertEqual(md.status_code, 200, md.text)
                self.assertIn("# 评审会", md.text)
                html = client.get(f"/api/meetings/{meeting_id}/minutes", params={"format": "html"})
                self.assertEqual(html.status_code, 200, html.text)
                self.assertIn("text/html", html.headers["content-type"])
                reopened = client.post(f"/api/meetings/{meeting_id}/status", json={"status": "live"})
                self.assertEqual(reopened.status_code, 200, reopened.text)
                listing = client.get("/api/meetings").json()["meetings"]
                self.assertEqual(listing[0]["status"], "live")


if __name__ == "__main__":
    unittest.main()
