"""Keep every organizer route inside the intended meeting data directory."""

import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import server
from app.storage import StoreA


class MeetingIdBoundaryTests(unittest.TestCase):
    def test_storage_rejects_path_like_meeting_ids(self) -> None:
        with TemporaryDirectory() as root:
            store = StoreA(root)
            for meeting_id in ("../outside", "nested/name", ".", ".."):
                with self.subTest(meeting_id=meeting_id):
                    with self.assertRaises(ValueError):
                        store.load(meeting_id)

    def test_legacy_rest_routes_reject_path_traversal(self) -> None:
        with TestClient(server.app) as client:
            self.assertEqual(client.get("/api/board", params={"meeting_id": "../outside"}).status_code, 422)
            with patch.object(server, "_drive"):
                self.assertEqual(client.post("/api/cli/push", json={
                    "meeting_id": "../outside", "text": "unsafe",
                }).status_code, 422)


if __name__ == "__main__":
    unittest.main()
