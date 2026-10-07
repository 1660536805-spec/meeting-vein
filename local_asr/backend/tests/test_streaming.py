from __future__ import annotations

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import pytest

from app.asr_runtime import RuntimeStatus
from app.config import Settings
from app import main as main_module
from app.main import create_app
from app.streaming_runtime import StreamingRuntime


class FakeModel:
    def generate(self, **kwargs):
        cache = kwargs["cache"]
        cache["calls"] = cache.get("calls", 0) + 1
        return [{"text": f"第{cache['calls']}块"}]


def test_model_is_warmed_before_reporting_ready():
    calls = []

    class TrackingModel(FakeModel):
        def generate(self, **kwargs):
            calls.append(kwargs["input"].shape[0])
            return super().generate(**kwargs)

    runtime = StreamingRuntime(Settings(), model_factory=lambda **_: TrackingModel())
    runtime.load()
    assert runtime.status().state == "ready"
    assert calls == [7_680, 7_680]


class StubFinalRuntime:
    def status(self):
        return RuntimeStatus("ready", "cpu", None)


def test_streaming_runtime_keeps_each_recording_cache_separate():
    runtime = StreamingRuntime(Settings(), model_factory=lambda **_: FakeModel())
    runtime.load()
    first, second = {}, {}
    frame = bytes(15_360)

    assert runtime.transcribe(frame, first) == "第1块"
    assert runtime.transcribe(frame, first) == "第2块"
    assert runtime.transcribe(frame, second) == "第1块"


def test_websocket_returns_ordered_partials_and_rejects_bad_frames():
    runtime = StreamingRuntime(Settings(), model_factory=lambda **_: FakeModel())
    runtime.load()
    app = create_app(runtime=StubFinalRuntime(), streaming_runtime=runtime)
    with TestClient(app) as client:
        with client.websocket_connect(
            "/v1/audio/stream", headers={"origin": "http://127.0.0.1:5173"}
        ) as socket:
            assert socket.receive_json()["type"] == "ready"
            socket.send_bytes(bytes(15_360))
            assert socket.receive_json() == {"type": "partial", "seq": 1, "text": "第1块"}
            socket.send_bytes(bytes(15_360))
            assert socket.receive_json() == {"type": "partial", "seq": 2, "text": "第2块"}
            socket.send_bytes(b"bad")
            assert socket.receive_json()["type"] == "error"


def test_websocket_cuts_a_final_segment_after_silence():
    class SilenceModel:
        def generate(self, **kwargs):
            cache = kwargs["cache"]
            cache["calls"] = cache.get("calls", 0) + 1
            return [{"text": "开始说话" if cache["calls"] == 1 else ""}]

    runtime = StreamingRuntime(Settings(), model_factory=lambda **_: SilenceModel())
    runtime.load()
    app = create_app(runtime=StubFinalRuntime(), streaming_runtime=runtime)
    with TestClient(app) as client:
        with client.websocket_connect(
            "/v1/audio/stream", headers={"origin": "http://127.0.0.1:5173"}
        ) as socket:
            assert socket.receive_json()["type"] == "ready"
            socket.send_bytes(bytes(15_360))
            assert socket.receive_json()["type"] == "partial"
            socket.send_bytes(bytes(15_360))
            assert socket.receive_json()["type"] == "partial"
            socket.send_bytes(bytes(15_360))
            final = socket.receive_json()
            assert final["type"] == "final"
            assert final["text"] == "开始说话"
            assert final["segment_id"].startswith("utt_local_stream_")
            assert final["end_offset_ms"] >= final["start_offset_ms"]


def test_websocket_flush_control_frame_returns_final_and_acknowledges():
    runtime = StreamingRuntime(Settings(), model_factory=lambda **_: FakeModel())
    runtime.load()
    app = create_app(runtime=StubFinalRuntime(), streaming_runtime=runtime)
    with TestClient(app) as client:
        with client.websocket_connect(
            "/v1/audio/stream", headers={"origin": "http://127.0.0.1:5173"}
        ) as socket:
            assert socket.receive_json()["type"] == "ready"
            socket.send_bytes(bytes(15_360))
            assert socket.receive_json()["type"] == "partial"
            socket.send_text('{"type": "flush"}')
            final = socket.receive_json()
            assert final["type"] == "final"
            assert final["text"] == "第1块"
            assert socket.receive_json() == {"type": "flushed"}


def test_websocket_resume_preserves_frame_sequence_and_unacked_final_segment():
    runtime = StreamingRuntime(Settings(), model_factory=lambda **_: FakeModel())
    runtime.load()
    app = create_app(runtime=StubFinalRuntime(), streaming_runtime=runtime)
    session_id = "resume_session_0123456789abcdef"
    with TestClient(app) as client:
        with client.websocket_connect(
            f"/v1/audio/stream?session_id={session_id}", headers={"origin": "http://127.0.0.1:5173"}
        ) as socket:
            ready = socket.receive_json()
            assert ready["seq"] == 0
            generation = ready["generation"]
            socket.send_bytes(bytes(15_360))
            assert socket.receive_json() == {"type": "partial", "seq": 1, "text": "第1块"}

        with client.websocket_connect(
            f"/v1/audio/stream?session_id={session_id}", headers={"origin": "http://127.0.0.1:5173"}
        ) as socket:
            ready = socket.receive_json()
            assert ready["seq"] == 1
            assert ready["generation"] == generation
            assert ready["partial_text"] == "第1块"
            socket.send_bytes(bytes(15_360))
            assert socket.receive_json() == {"type": "partial", "seq": 2, "text": "第2块"}
            socket.send_text('{"type":"flush"}')
            final = socket.receive_json()
            assert final["text"] == "第1块第2块"
            first_segment_id = final["segment_id"]
            assert generation in first_segment_id
            assert final["seq"] == 2
            assert socket.receive_json() == {"type": "flushed"}

        with client.websocket_connect(
            f"/v1/audio/stream?session_id={session_id}", headers={"origin": "http://127.0.0.1:5173"}
        ) as socket:
            assert socket.receive_json()["type"] == "ready"
            replay = socket.receive_json()
            assert replay["type"] == "final"
            assert replay["segment_id"] == first_segment_id
            socket.send_text('{"type":"ack","segment_id":"' + first_segment_id + '"}')

        with client.websocket_connect(
            f"/v1/audio/stream?session_id={session_id}", headers={"origin": "http://127.0.0.1:5173"}
        ) as socket:
            assert socket.receive_json()["type"] == "ready"


def test_websocket_rejects_unapproved_origin():
    runtime = StreamingRuntime(Settings(), model_factory=lambda **_: FakeModel())
    runtime.load()
    app = create_app(runtime=StubFinalRuntime(), streaming_runtime=runtime)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(
                "/v1/audio/stream", headers={"origin": "http://evil.example"}
            ):
                pass
        assert caught.value.code == 1008


def test_websocket_rejects_new_session_when_all_session_slots_are_active(monkeypatch):
    runtime = StreamingRuntime(Settings(), model_factory=lambda **_: FakeModel())
    runtime.load()
    app = create_app(runtime=StubFinalRuntime(), streaming_runtime=runtime)
    monkeypatch.setattr(main_module, "_STREAM_SESSION_LIMIT", 1)
    with TestClient(app) as client:
        with client.websocket_connect(
            "/v1/audio/stream?session_id=active_session_0123456789abcdef",
            headers={"origin": "http://127.0.0.1:5173"},
        ) as active:
            assert active.receive_json()["type"] == "ready"
            with client.websocket_connect(
                "/v1/audio/stream?session_id=other_session_0123456789abcdef",
                headers={"origin": "http://127.0.0.1:5173"},
            ) as rejected:
                assert rejected.receive_json() == {
                    "type": "error", "message": "实时识别会话已满，请稍后重试",
                }
