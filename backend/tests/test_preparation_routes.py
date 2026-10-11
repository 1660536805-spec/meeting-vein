import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import server
from app.preparation import PreparationStore
from app.storage import StoreA


class PreparationRouteTests(unittest.TestCase):
    def test_upload_suggest_confirm_and_resolve_question(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StoreA(directory)
            store.create_meeting("mtg_prep", "方案会", agenda=["上线范围"])
            preparation = PreparationStore(Path(directory) / "prep.sqlite3")
            with patch.object(server, "store_a", store), patch.object(server, "preparation_store", preparation, create=True), TestClient(server.app) as client:
                uploaded = client.post("/api/meetings/mtg_prep/materials", json={
                    "filename": "plan.md",
                    "content_base64": base64.b64encode("我们是否先做小范围试点？\n".encode()).decode(),
                })
                self.assertEqual(uploaded.status_code, 200, uploaded.text)
                view = client.get("/api/meetings/mtg_prep/preparation")
                self.assertEqual(view.status_code, 200, view.text)
                self.assertEqual(len(view.json()["materials"]), 1)
                self.assertEqual(len(view.json()["questions"]), 0)
                suggestion = next(item for item in view.json()["suggestions"] if item["source_kind"] == "material")
                confirmed = client.post("/api/meetings/mtg_prep/questions", json={
                    "event_id": "q-1", "text": suggestion["text"],
                    "source_kind": suggestion["source_kind"], "source_ref": suggestion["source_ref"],
                })
                self.assertEqual(confirmed.status_code, 200, confirmed.text)
                question = confirmed.json()["question"]
                self.assertEqual(question["status"], "pending")
                source = client.get(f"/api/meetings/mtg_prep/material-source?source_ref={suggestion['source_ref']}")
                self.assertEqual(source.status_code, 200, source.text)
                self.assertEqual(source.json()["text"], "我们是否先做小范围试点？")
                fake = client.get(f"/api/meetings/mtg_prep/material-source?source_ref={uploaded.json()['material']['id']}:line:999")
                self.assertEqual(fake.status_code, 404)
                resolved = client.patch(f"/api/meetings/mtg_prep/questions/{question['id']}", json={
                    "expected_version": 1, "status": "resolved",
                })
                self.assertEqual(resolved.status_code, 200, resolved.text)
                stale = client.patch(f"/api/meetings/mtg_prep/questions/{question['id']}", json={
                    "expected_version": 1, "status": "dismissed",
                })
                self.assertEqual(stale.status_code, 409)
                snapshot = client.get("/api/meetings/mtg_prep/snapshot-file")
                self.assertEqual(snapshot.json()["questions"][0]["status"], "resolved")


if __name__ == "__main__":
    unittest.main()
