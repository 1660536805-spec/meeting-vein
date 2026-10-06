import threading
import time
from array import array
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.adapters import NoSpeechError
from app.asr_runtime import (
    AsrRuntime,
    ModelLoadError,
    ModelNotReadyError,
)
from app.audio_gateway import PreparedAudio
from app.config import Settings


class FakeModel:
    def __init__(self, result=None) -> None:
        self.result = (
            result
            if result is not None
            else [{"text": "<|zh|><|NEUTRAL|><|Speech|>今天开会"}]
        )
        self.calls: list[dict[str, object]] = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class RecordingFactory:
    def __init__(self, *, fail_devices: set[str] | None = None, model=None) -> None:
        self.fail_devices = fail_devices or set()
        self.model = model or FakeModel()
        self.devices: list[str] = []

    def __call__(self, **kwargs):
        device = kwargs["device"]
        self.devices.append(device)
        if device in self.fail_devices:
            raise RuntimeError(f"{device} unavailable")
        return self.model


class OverlapDetectingModel(FakeModel):
    def __init__(self) -> None:
        super().__init__()
        self._active = 0
        self.max_concurrent_calls = 0
        self._counter_lock = threading.Lock()

    def generate(self, **kwargs):
        with self._counter_lock:
            self._active += 1
            self.max_concurrent_calls = max(self.max_concurrent_calls, self._active)
        time.sleep(0.03)
        with self._counter_lock:
            self._active -= 1
        return self.result


def audio_fixture() -> PreparedAudio:
    return PreparedAudio(
        pcm=array("f", [0.0] * 1_600),
        sample_rate=16_000,
        duration_ms=100,
    )


def mps_settings() -> Settings:
    return Settings(prefer_mps=True)


def cpu_settings() -> Settings:
    return Settings(prefer_mps=False)


def ready_runtime(model) -> AsrRuntime:
    runtime = AsrRuntime(
        settings=cpu_settings(),
        model_factory=RecordingFactory(model=model),
        pcm_converter=lambda pcm: pcm,
    )
    runtime.load()
    return runtime


def test_load_retries_cpu_once_after_mps_failure() -> None:
    factory = RecordingFactory(fail_devices={"mps"})
    runtime = AsrRuntime(
        settings=mps_settings(),
        model_factory=factory,
        machine="arm64",
    )

    runtime.load()

    assert factory.devices == ["mps", "cpu"]
    assert runtime.status().device == "cpu"
    assert runtime.status().state == "ready"


def test_load_reports_error_when_both_devices_fail() -> None:
    runtime = AsrRuntime(
        settings=mps_settings(),
        model_factory=RecordingFactory(fail_devices={"mps", "cpu"}),
        machine="arm64",
    )

    with pytest.raises(ModelLoadError):
        runtime.load()

    assert runtime.status().state == "error"
    assert runtime.status().device is None
    assert runtime.status().error == "RuntimeError: cpu unavailable"


def test_transcribe_before_ready_raises_model_not_ready() -> None:
    runtime = AsrRuntime(cpu_settings(), model_factory=RecordingFactory())

    with pytest.raises(ModelNotReadyError):
        runtime.transcribe(audio_fixture())


def test_empty_model_result_raises_no_speech() -> None:
    runtime = ready_runtime(FakeModel(result=[]))

    with pytest.raises(NoSpeechError):
        runtime.transcribe(audio_fixture())


def test_empty_cleaned_text_raises_no_speech() -> None:
    runtime = ready_runtime(FakeModel(result=[{"text": "<|zh|><|Speech|>"}]))

    with pytest.raises(NoSpeechError):
        runtime.transcribe(audio_fixture())


def test_transcribe_maps_tags_and_audio_bounds() -> None:
    model = FakeModel()
    runtime = ready_runtime(model)

    result = runtime.transcribe(audio_fixture())

    assert result.text == "今天开会"
    assert result.language == "zh"
    assert result.tags == ["zh", "NEUTRAL", "Speech"]
    assert (result.start_ms, result.end_ms) == (0, 100)
    assert model.calls[0]["fs"] == 16_000
    assert model.calls[0]["language"] == "auto"
    assert model.calls[0]["use_itn"] is True


def test_shared_model_inference_is_serialized() -> None:
    model = OverlapDetectingModel()
    runtime = ready_runtime(model)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(runtime.transcribe, [audio_fixture(), audio_fixture()]))

    assert model.max_concurrent_calls == 1
