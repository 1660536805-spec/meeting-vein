from array import array

import pytest
from fastapi.testclient import TestClient

from app.adapters import NoSpeechError
from app.asr_runtime import ModelNotReadyError, RuntimeStatus
from app.audio_gateway import AudioGateway, PreparedAudio
from app.config import Settings
from app.main import create_app
from app.schemas import TranscriptionResult
from tests.audio_fixtures import make_wav


class StubGateway:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.received: list[bytes] = []

    def prepare(self, data: bytes) -> PreparedAudio:
        self.received.append(data)
        if self.error:
            raise self.error
        return PreparedAudio(array("f", [0.0] * 1_600), 16_000, 100)


class StubRuntime:
    def __init__(
        self,
        *,
        state: str = "loading",
        result: TranscriptionResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.state = state
        self.result = result or TranscriptionResult(
            text="今天开会",
            language="zh",
            start_ms=0,
            end_ms=100,
            audio_duration_ms=100,
        )
        self.error = error

    def load(self) -> None:
        return None

    def status(self) -> RuntimeStatus:
        device = "cpu" if self.state == "ready" else None
        return RuntimeStatus(self.state, device, None)

    def transcribe(self, audio: PreparedAudio, language: str = "auto") -> TranscriptionResult:
        if self.error:
            raise self.error
        return self.result


def make_client(
    runtime: StubRuntime,
    gateway=None,
    *,
    settings: Settings | None = None,
) -> TestClient:
    app = create_app(
        settings=settings or Settings(),
        runtime=runtime,
        gateway=gateway or StubGateway(),
    )
    return TestClient(app)


@pytest.fixture
def client():
    with make_client(
        StubRuntime(state="loading", error=ModelNotReadyError())
    ) as test_client:
        yield test_client


@pytest.fixture
def ready_client():
    with make_client(StubRuntime(state="ready")) as test_client:
        yield test_client


@pytest.fixture
def no_speech_client():
    with make_client(
        StubRuntime(state="ready", error=NoSpeechError())
    ) as test_client:
        yield test_client


def test_health_reports_service_up_while_model_is_loading(client) -> None:
    assert client.get("/health").json() == {"ok": True, "model_status": "loading"}


def test_models_status_exposes_state_and_device(ready_client) -> None:
    assert ready_client.get("/models/status").json() == {
        "ok": True,
        "model": "iic/SenseVoiceSmall",
        "vad_model": "fsmn-vad",
        "state": "ready",
        "device": "cpu",
        "error": None,
        "streaming_state": "not_loaded",
        "streaming_error": None,
    }


def test_transcription_returns_model_not_ready_contract(client) -> None:
    response = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", make_wav(), "audio/wav")},
    )

    assert response.status_code == 503
    assert response.json() == {
        "ok": False,
        "error": {
            "code": "model_not_ready",
            "message": "本地语音模型尚未完成加载",
        },
    }


def test_transcription_returns_result_and_norm_event(ready_client) -> None:
    response = ready_client.post(
        "/v1/audio/transcriptions",
        data={"meeting_id": "mtg_demo", "seq": "3"},
        files={"file": ("clip.wav", make_wav(), "audio/wav")},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["event"]["text"] == body["transcription"]["text"]
    assert body["event"]["is_final"] is True
    assert body["event"]["seq"] == 3


def test_no_speech_returns_422_without_norm_event(no_speech_client) -> None:
    response = no_speech_client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", make_wav(), "audio/wav")},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_speech"
    assert "event" not in response.json()


def test_invalid_audio_error_does_not_include_decoder_stderr() -> None:
    settings = Settings(max_upload_bytes=1_000_000)
    with make_client(
        StubRuntime(state="ready"),
        AudioGateway(settings),
        settings=settings,
    ) as test_client:
        response = test_client.post(
            "/v1/audio/transcriptions",
            files={
                "file": (
                    "clip.wav",
                    b"decoder secret stderr",
                    "audio/wav",
                )
            },
        )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_audio"
    assert "decoder secret stderr" not in response.text


def test_upload_over_byte_limit_is_rejected_before_gateway() -> None:
    gateway = StubGateway()
    settings = Settings(max_upload_bytes=8)
    with make_client(
        StubRuntime(state="ready"), gateway, settings=settings
    ) as test_client:
        response = test_client.post(
            "/v1/audio/transcriptions",
            files={"file": ("clip.wav", b"x" * 9, "audio/wav")},
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "audio_too_large"
    assert gateway.received == []


def test_cors_accepts_only_loopback_frontend_origins(client) -> None:
    allowed = client.options(
        "/health",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    denied = client.options(
        "/health",
        headers={
            "Origin": "http://192.168.1.20:5173",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    assert "access-control-allow-origin" not in denied.headers
