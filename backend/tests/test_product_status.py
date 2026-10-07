import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import server
from app.llm import MockLLM, OpenAIClient
from app.storage import StoreA, StoreB


class ProductStatusTests(unittest.TestCase):
    def test_status_reports_live_components_persistence_and_retry_count(self):
        with tempfile.TemporaryDirectory() as root:
            store_a = StoreA(root + "/boards")
            store_b = StoreB(root + "/metadata")
            store_b.put("pending", {"kind": "pending_batch", "meeting_id": "mtg_status"})
            with (patch.object(server, "store_a", store_a),
                  patch.object(server, "store_b", store_b),
                  patch.object(server, "agent", SimpleNamespace(llm=MockLLM())),
                  patch.object(server, "_probe_asr_status", return_value={
                      "streaming": {"state": "ready"}, "final": {"state": "ready"},
                  }),
                  TestClient(server.app) as client):
                status = client.get("/api/status").json()
            self.assertEqual(status["llm_mode"], "mock")
            self.assertEqual(status["llm_instance"]["type"], "mock")
            self.assertEqual(status["asr"]["streaming"]["state"], "ready")
            self.assertEqual(status["asr"]["final"]["state"], "ready")
            self.assertEqual(status["persistence"]["store_a"], "ready")
            self.assertEqual(status["persistence"]["store_b"], "ready")
            self.assertEqual(status["persistence"]["pending_retries"], 1)

    def test_configured_environment_does_not_override_mock_instance_report(self):
        fake_real_client = object.__new__(OpenAIClient)
        with patch.object(server, "agent", SimpleNamespace(llm=MockLLM())), TestClient(server.app) as client:
            mock = client.get("/api/status").json()
        with patch.object(server, "agent", SimpleNamespace(llm=fake_real_client)), TestClient(server.app) as client:
            real = client.get("/api/status").json()
        self.assertEqual(mock["llm_mode"], "mock")
        self.assertEqual(real["llm_mode"], "real")
        self.assertEqual(real["llm_instance"]["type"], "openai_compatible")

    def test_local_snapshot_file_includes_readonly_board_and_source_records(self):
        with tempfile.TemporaryDirectory() as root:
            store_a = StoreA(root + "/boards")
            store_b = StoreB(root + "/metadata")
            store_a.create_meeting("mtg_snapshot", "本地快照", ["议题甲"])
            store_b.put("utt_source", {"kind": "utt", "meeting_id": "mtg_snapshot", "text": "原话来源"})
            with (patch.object(server, "store_a", store_a), patch.object(server, "store_b", store_b),
                  TestClient(server.app) as client):
                response = client.get("/api/meetings/mtg_snapshot/snapshot-file")
            self.assertEqual(response.status_code, 200)
            self.assertIn("attachment", response.headers["content-disposition"])
            document = response.json()
            self.assertTrue(document["readonly"])
            self.assertEqual(document["meeting"]["agenda"], ["议题甲"])
            self.assertIn("原话来源", [record["text"] for record in document["utterances"]])


if __name__ == "__main__":
    unittest.main()
