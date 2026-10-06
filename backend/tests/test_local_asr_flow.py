"""The ASR HTTP event contract flowing into the organizer board."""

import io
import unittest
import wave
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import server
from app.llm import MockLLM
from app.orchestrator import BoardAgent
from app.storage import StoreA, StoreB
from app.utterance_ingest import UtteranceIngestor
from app.ws.bus import EventBus


def wav_bytes() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 1600)
    return output.getvalue()


class StubAsrResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self) -> dict:
        return self._body


class StubAsrClient:
    def __init__(self, state: str = "ready"):
        self.state = state

    def post(self, path: str, *, files: dict, data: dict | None = None) -> StubAsrResponse:
        assert path == "/v1/audio/transcriptions"
        filename, audio, media_type = files["file"]
        if self.state != "ready":
            return StubAsrResponse(503, {"ok": False, "error": {"code": "model_not_ready"}})
        if not filename.endswith(".wav") or media_type != "audio/wav" or not audio.startswith(b"RIFF"):
            return StubAsrResponse(415, {"ok": False, "error": {"code": "unsupported_audio"}})
        meeting_id = (data or {}).get("meeting_id", "mtg_demo")
        event = {
            "utterance_id": "utt_stub_wav_1", "meeting_id": meeting_id,
            "session_id": None, "seq": 1,
            "speaker": {"speaker_ref": "local:user", "display_name": "本地发言人", "is_resolved": False},
            "text": "今天完成会议纪要", "language": "zh", "start_offset_ms": 0,
            "end_offset_ms": 100, "received_at_ms": 1,
            "is_final": True, "is_partial": False, "source": "local_sensevoice",
        }
        return StubAsrResponse(200, {"ok": True, "event": event, "transcription": {"text": event["text"]}})


class LocalAsrFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.store_a = StoreA(self.directory.name)
        self.store_b = StoreB(self.directory.name)
        self.bus = EventBus()
        self.subscriber = self.bus.subscribe()
        self.agent = BoardAgent(self.store_a, self.store_b, MockLLM())
        self.patches = [
            patch.object(server, "store_a", self.store_a),
            patch.object(server, "store_b", self.store_b),
            patch.object(server, "bus", self.bus),
            patch.object(server, "agent", self.agent),
            patch.object(server, "utterance_ingestor", UtteranceIngestor(self.store_a, self.store_b, server._drive)),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(server.app)

    def tearDown(self) -> None:
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.directory.cleanup()

    def test_wav_to_board_metadata_ws_and_duplicate(self) -> None:
        asr_response = StubAsrClient().post(
            "/v1/audio/transcriptions",
            files={"file": ("clip.wav", wav_bytes(), "audio/wav")},
            data={"meeting_id": "mtg_demo"},
        )
        self.assertEqual(asr_response.status_code, 200)
        event = asr_response.json()["event"]
        accepted = self.client.post("/api/utterances", json=event)
        self.assertEqual(accepted.json(), {"ok": True, "utterance_id": event["utterance_id"], "duplicate": False})
        cells = self.client.get("/api/board", params={"meeting_id": "mtg_demo"}).json()["cells"]
        evidence = [cell for cell in cells if cell.get("data", {}).get("metadata_refs")]
        self.assertTrue(evidence)
        reference = evidence[0]["data"]["metadata_refs"][0]
        self.assertEqual(self.store_b.get(reference)["text"], event["text"])
        self.assertIn("board.update", [self.subscriber.get_nowait()["type"] for _ in range(self.subscriber.qsize())])

        before = [(cell["id"], cell.get("data", {}).get("mention_count")) for cell in cells]
        again = self.client.post("/api/utterances", json=event)
        self.assertTrue(again.json()["duplicate"])
        after_cells = self.client.get("/api/board", params={"meeting_id": "mtg_demo"}).json()["cells"]
        self.assertEqual(before, [(cell["id"], cell.get("data", {}).get("mention_count")) for cell in after_cells])
        self.assertEqual(self.subscriber.qsize(), 0)

    def test_asr_errors_do_not_disable_manual_composer_route(self) -> None:
        unsupported = StubAsrClient().post(
            "/v1/audio/transcriptions", files={"file": ("bad.mp3", b"invalid", "audio/mpeg")}
        )
        not_ready = StubAsrClient("loading").post(
            "/v1/audio/transcriptions", files={"file": ("clip.wav", wav_bytes(), "audio/wav")}
        )
        self.assertEqual(unsupported.json()["error"]["code"], "unsupported_audio")
        self.assertEqual(not_ready.json()["error"]["code"], "model_not_ready")
        manual = self.client.post("/api/cli/push", json={"meeting_id": "mtg_demo", "text": "手动输入仍然有效"})
        self.assertEqual(manual.status_code, 200)
        self.assertTrue(self.client.get("/api/board?meeting_id=mtg_demo").json()["cells"])


if __name__ == "__main__":
    unittest.main()
