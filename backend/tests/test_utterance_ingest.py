"""Behavioral tests for the normalized utterance HTTP boundary."""

import dataclasses
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import config, server
from app.llm import MockLLM
from app.orchestrator import BoardAgent
from app.storage import StoreA, StoreB
from app.utterance_ingest import UtteranceIngestor
from app.ws.bus import EventBus


EVENT = {
    "utterance_id": "utt_local_1",
    "meeting_id": "mtg_demo",
    "session_id": None,
    "seq": 1,
    "speaker": {
        "speaker_ref": "local:user",
        "display_name": "本地发言人",
        "is_resolved": False,
    },
    "text": "我们先完成原型验收",
    "language": "zh",
    "start_offset_ms": 0,
    "end_offset_ms": 1200,
    "received_at_ms": 1,
    "is_final": True,
    "is_partial": False,
    "source": "local_sensevoice",
}


class UtteranceRouteValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(server.app)

    def tearDown(self) -> None:
        self.client.close()

    def test_invalid_inputs_never_reach_board_storage(self) -> None:
        invalid_changes = (
            {"meeting_id": "../outside"},
            {"text": "  "},
            {"is_partial": True},
            {"is_final": False},
            {"start_offset_ms": -1},
            {"end_offset_ms": -1},
            {"end_offset_ms": 0, "start_offset_ms": 10},
            {"text": "x" * 10001},
        )
        for change in invalid_changes:
            with self.subTest(change=change):
                response = self.client.post("/api/utterances", json={**EVENT, **change})
                self.assertEqual(response.status_code, 422)

    def test_status_discloses_mode_but_not_key(self) -> None:
        # Config 为 frozen dataclass，不能再 patch 实例字段；改为整体替换模块级单例（语义不变）。
        mock_cfg = dataclasses.replace(config.CONFIG, llm_enabled=False, llm_api_key="secret-for-test")
        with patch.object(config, "CONFIG", mock_cfg):
            response = self.client.get("/api/status")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["llm_mode"], "mock")
            self.assertNotIn("secret-for-test", response.text)

        real_cfg = dataclasses.replace(config.CONFIG, llm_enabled=True, llm_api_key="secret-for-test")
        with patch.object(config, "CONFIG", real_cfg):
            response = self.client.get("/api/status")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["llm_mode"], "configured")
            self.assertNotIn("secret-for-test", response.text)

    def test_accepted_event_updates_board_and_metadata(self) -> None:
        with TemporaryDirectory() as root:
            store_a = StoreA(root)
            store_b = StoreB(root)
            bus = EventBus()
            subscriber = bus.subscribe()
            agent = BoardAgent(store_a, store_b, MockLLM())
            with patch.object(server, "store_a", store_a), patch.object(
                server, "store_b", store_b
            ), patch.object(server, "bus", bus), patch.object(
                server, "agent", agent
            ), patch.object(
                server, "utterance_ingestor", UtteranceIngestor(store_a, store_b, server._drive)
            ):
                response = self.client.post("/api/utterances", json=EVENT)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["utterance_id"], "utt_local_1")
                self.assertFalse(response.json()["duplicate"])
                cells = self.client.get("/api/board?meeting_id=mtg_demo").json()["cells"]
                references = [ref for cell in cells for ref in cell.get("data", {}).get("metadata_refs", [])]
                self.assertEqual(len(references), 1)
                records = self.client.get(f"/api/metadata?ids={references[0]}").json()["records"]
                self.assertEqual(records[0]["text"], EVENT["text"])
                self.assertEqual(records[0]["source_utterance_id"], "utt_local_1")
                event_types = [subscriber.get_nowait()["type"] for _ in range(subscriber.qsize())]
                self.assertIn("board.update", event_types)

class UtteranceIngestorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.store_a = StoreA(self.directory.name)
        self.store_b = StoreB(self.directory.name)
        self.bus = EventBus()
        self.events = self.bus.subscribe()
        self.driven = []

        async def drive(meeting_id, raw_utterances):
            self.driven.append((meeting_id, raw_utterances[0]))
            await self.bus.publish({"type": "board.update", "graph_id": meeting_id})

        self.drive = drive

    def tearDown(self) -> None:
        self.directory.cleanup()

    async def test_duplicate_event_is_durable_and_not_broadcast_twice(self) -> None:
        from app.utterance_ingest import IncomingUtterance, UtteranceIngestor

        event = IncomingUtterance(**EVENT)
        first = await UtteranceIngestor(self.store_a, self.store_b, self.drive).ingest(event)
        self.assertEqual(first, {"ok": True, "utterance_id": "utt_local_1", "duplicate": False})
        self.assertEqual(self.events.qsize(), 1)
        self.assertEqual(len(self.driven), 1)
        self.assertEqual(self.driven[0][0], "mtg_demo")
        meta_id = self.driven[0][1].utterance_id
        self.assertTrue(meta_id.startswith("utt_evt_"))
        self.assertEqual(self.store_b.get(meta_id)["text"], EVENT["text"])
        self.assertEqual(self.store_b.get(meta_id)["processing_state"], "done")

        restarted = UtteranceIngestor(StoreA(self.directory.name), StoreB(self.directory.name), self.drive)
        again = await restarted.ingest(event)
        self.assertEqual(again, {"ok": True, "utterance_id": "utt_local_1", "duplicate": True})
        self.assertEqual(len(self.driven), 1)
        self.assertEqual(self.events.qsize(), 1)

    async def test_same_external_id_in_other_meeting_gets_distinct_metadata(self) -> None:
        from app.utterance_ingest import IncomingUtterance, UtteranceIngestor

        ingestor = UtteranceIngestor(self.store_a, self.store_b, self.drive)
        await ingestor.ingest(IncomingUtterance(**EVENT))
        await ingestor.ingest(IncomingUtterance(**{**EVENT, "meeting_id": "mtg_other"}))
        self.assertEqual(len(self.driven), 2)
        self.assertNotEqual(self.driven[0][1].utterance_id, self.driven[1][1].utterance_id)
        self.assertEqual(self.store_b.get(self.driven[1][1].utterance_id)["meeting_id"], "mtg_other")

    async def test_failed_drive_can_retry_original_event_id(self) -> None:
        from app.utterance_ingest import IncomingUtterance, UtteranceIngestor

        attempts = 0

        async def flaky_drive(meeting_id, raw_utterances):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("agent unavailable")
            self.driven.append((meeting_id, raw_utterances[0]))

        ingestor = UtteranceIngestor(self.store_a, self.store_b, flaky_drive)
        event = IncomingUtterance(**EVENT)
        with self.assertRaisesRegex(RuntimeError, "agent unavailable"):
            await ingestor.ingest(event)
        result = await ingestor.ingest(event)
        self.assertFalse(result["duplicate"])
        self.assertEqual(attempts, 2)
        self.assertEqual(self.store_b.get(self.driven[0][1].utterance_id)["processing_state"], "done")

    async def test_restart_after_graph_write_does_not_repeat_mention(self) -> None:
        from app.utterance_ingest import IncomingUtterance

        store_a = StoreA(self.directory.name)
        agent = BoardAgent(store_a, self.store_b, MockLLM())

        async def drive(meeting_id, raw_utterances):
            await agent.run(meeting_id, raw_utterances, [])

        original_put = self.store_b.put
        failed_done = False

        def fail_first_done(meta_id, record):
            nonlocal failed_done
            if record.get("processing_state") == "done" and not failed_done:
                failed_done = True
                raise OSError("simulated crash after graph write")
            original_put(meta_id, record)

        with patch.object(self.store_b, "put", side_effect=fail_first_done):
            with self.assertRaisesRegex(OSError, "after graph write"):
                await UtteranceIngestor(store_a, self.store_b, drive).ingest(IncomingUtterance(**EVENT))

        before = store_a.load("mtg_demo")
        mentions_before = sum(cell.get("data", {}).get("mention_count", 0) for cell in before)
        restarted_a = StoreA(self.directory.name)
        restarted_b = StoreB(self.directory.name)
        restarted_agent = BoardAgent(restarted_a, restarted_b, MockLLM())

        async def restarted_drive(meeting_id, raw_utterances):
            await restarted_agent.run(meeting_id, raw_utterances, [])

        result = await UtteranceIngestor(restarted_a, restarted_b, restarted_drive).ingest(IncomingUtterance(**EVENT))
        mentions_after = sum(cell.get("data", {}).get("mention_count", 0) for cell in restarted_a.load("mtg_demo"))
        self.assertEqual(result["duplicate"], True)
        self.assertEqual(mentions_after, mentions_before)

    async def test_debounce_coalesces_window_into_single_drive(self) -> None:
        from app.utterance_ingest import IncomingUtterance, UtteranceIngestor

        batches = []

        async def drive(meeting_id, raw_utterances):
            batches.append((meeting_id, [u.utterance_id for u in raw_utterances]))

        ingestor = UtteranceIngestor(self.store_a, self.store_b, drive, debounce_ms=50)
        events = [IncomingUtterance(**{**EVENT, "utterance_id": f"utt_b{i}", "seq": i})
                  for i in range(3)]
        for event in events:
            result = await ingestor.ingest(event)
            self.assertEqual(result["duplicate"], False)
            self.assertTrue(result["batched"])
        # 窗口未到：尚未驱动。
        self.assertEqual(batches, [])

        await ingestor.flush_all()
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0][1]), 3)
        for meta_id in batches[0][1]:
            self.assertEqual(self.store_b.get(meta_id)["processing_state"], "done")

    async def test_debounce_duplicate_within_window_not_requeued(self) -> None:
        from app.utterance_ingest import IncomingUtterance, UtteranceIngestor

        batches = []

        async def drive(meeting_id, raw_utterances):
            batches.append([u.utterance_id for u in raw_utterances])

        ingestor = UtteranceIngestor(self.store_a, self.store_b, drive, debounce_ms=50)
        event = IncomingUtterance(**EVENT)
        first = await ingestor.ingest(event)
        second = await ingestor.ingest(event)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        await ingestor.flush_all()
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 1)

    async def test_debounce_timer_fires_without_explicit_flush(self) -> None:
        import asyncio

        from app.utterance_ingest import IncomingUtterance, UtteranceIngestor

        batches = []

        async def drive(meeting_id, raw_utterances):
            batches.append((meeting_id, len(raw_utterances)))

        ingestor = UtteranceIngestor(self.store_a, self.store_b, drive, debounce_ms=30)
        await ingestor.ingest(IncomingUtterance(**EVENT))
        self.assertEqual(batches, [])
        await asyncio.sleep(0.15)
        self.assertEqual(batches, [("mtg_demo", 1)])


if __name__ == "__main__":
    unittest.main()
