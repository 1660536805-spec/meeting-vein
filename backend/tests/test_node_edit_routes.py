import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import server
from app.storage import StoreA


class NodeEditRouteTests(unittest.TestCase):
    def test_patch_conflict_and_merge_preview_apply_contract(self):
        with tempfile.TemporaryDirectory() as root:
            store = StoreA(root)
            store._schemas["route_meeting"] = "amo.board/v2"
            store.save("route_meeting", [
                {"id": "n_issue_root", "shape": "amo-node", "data": {
                    "type": "issue", "label": "议题", "parent_id": None,
                    "metadata_refs": [], "importance": {"level": "normal"}}},
                {"id": "keep", "shape": "amo-node", "data": {
                    "type": "point", "label": "保留", "parent_id": "n_issue_root",
                    "metadata_refs": ["m1"], "importance": {"level": "normal"}}},
                {"id": "duplicate", "shape": "amo-node", "data": {
                    "type": "point", "label": "重复", "parent_id": "n_issue_root",
                    "metadata_refs": ["m2"], "importance": {"level": "normal"}}},
                {"id": "merge_keep", "shape": "amo-node", "data": {
                    "type": "point", "label": "合并保留", "parent_id": "n_issue_root",
                    "metadata_refs": [], "importance": {"level": "normal"}}},
                {"id": "merge_duplicate", "shape": "amo-node", "data": {
                    "type": "point", "label": "待合并", "parent_id": "n_issue_root",
                    "metadata_refs": ["m3"], "importance": {"level": "normal"}}},
            ])
            store.flush("route_meeting", force=True)
            with patch.object(server, "store_a", store), TestClient(server.app) as client:
                version = store.version("route_meeting")
                response = client.patch("/api/board/route_meeting/nodes/keep", json={
                    "expected_version": version, "fields": {"label": "人工确认"}, "reason": "主持人确认",
                })
                self.assertEqual(response.status_code, 200, response.text)
                stale = client.patch("/api/board/route_meeting/nodes/keep", json={
                    "expected_version": version, "fields": {"label": "过期文本"},
                })
                self.assertEqual(stale.status_code, 409)
                self.assertEqual(stale.json()["current_version"], version + 1)
                stale_undo = client.post("/api/board/route_meeting/rollback", json={
                    "cell_id": "keep", "version": version, "expected_version": version,
                })
                self.assertEqual(stale_undo.status_code, 409)

                expected = version + 1
                payload = {"duplicate_id": "duplicate", "survivor_id": "keep",
                           "expected_version": expected, "reason": "确认重复"}
                preview = client.post("/api/board/route_meeting/merge-preview", json=payload)
                self.assertEqual(preview.status_code, 200, preview.text)
                self.assertEqual(preview.json()["transferred_metadata_refs"], ["m2"])
                merged = client.post("/api/board/route_meeting/merge", json=payload)
                self.assertEqual(merged.status_code, 422)  # Manual fields require review before destructive merge.
                self.assertIn("keep", client.post("/api/board/route_meeting/merge-preview", json={
                    **payload, "duplicate_id": "duplicate",
                }).json()["protected_node_ids"])
                safe_payload = {**payload, "duplicate_id": "merge_duplicate", "survivor_id": "merge_keep"}
                safe_preview = client.post("/api/board/route_meeting/merge-preview", json=safe_payload)
                self.assertEqual(safe_preview.status_code, 200, safe_preview.text)
                safe_merge = client.post("/api/board/route_meeting/merge", json=safe_payload)
                self.assertEqual(safe_merge.status_code, 200, safe_merge.text)
                self.assertEqual(safe_merge.json()["version"], expected + 1)
                undone = client.post("/api/board/route_meeting/undo", json={
                    "operation_version": safe_merge.json()["version"],
                    "expected_version": safe_merge.json()["version"],
                })
                self.assertEqual(undone.status_code, 200, undone.text)
                self.assertIn("merge_duplicate", {cell["id"] for cell in undone.json()["cells"]})


if __name__ == "__main__":
    unittest.main()
