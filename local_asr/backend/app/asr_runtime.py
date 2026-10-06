import os
import platform
import threading
from array import array
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from app.adapters import NoSpeechError, clean_sensevoice_text, normalize_language
from app.audio_gateway import PreparedAudio
from app.config import Settings
from app.schemas import TranscriptionResult


RuntimeState = Literal["not_loaded", "loading", "ready", "error"]


class SpeechModel(Protocol):
    def generate(self, **kwargs: Any) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    state: RuntimeState
    device: str | None
    error: str | None


class ModelNotReadyError(RuntimeError):
    code = "model_not_ready"
    message = "本地语音模型尚未完成加载"

    def __init__(self) -> None:
        super().__init__(self.message)


class ModelLoadError(RuntimeError):
    code = "model_load_failed"
    message = "本地语音模型加载失败"

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(self.message)


def _default_model_factory(**kwargs: Any) -> SpeechModel:
    cache_dir = kwargs.pop("cache_dir", None)
    if cache_dir is not None:
        os.environ.setdefault("MODELSCOPE_CACHE", str(cache_dir))
    from funasr import AutoModel

    return AutoModel(**kwargs)


def _to_numpy(pcm: array[float]) -> Any:
    import numpy as np

    return np.asarray(pcm, dtype=np.float32)


class AsrRuntime:
    """Own one FunASR model and serialize all access to it."""

    def __init__(
        self,
        settings: Settings,
        *,
        model_factory: Callable[..., SpeechModel] = _default_model_factory,
        pcm_converter: Callable[[array[float]], Any] = _to_numpy,
        machine: str | None = None,
    ) -> None:
        self._settings = settings
        self._model_factory = model_factory
        self._pcm_converter = pcm_converter
        self._machine = machine or platform.machine()
        self._state: RuntimeState = "not_loaded"
        self._device: str | None = None
        self._error: str | None = None
        self._model: SpeechModel | None = None
        self._state_lock = threading.Lock()
        self._inference_lock = threading.Lock()

    def status(self) -> RuntimeStatus:
        with self._state_lock:
            return RuntimeStatus(self._state, self._device, self._error)

    def load(self) -> None:
        with self._state_lock:
            if self._state == "ready":
                return
            self._state = "loading"
            self._device = None
            self._error = None

        devices = ["cpu"]
        if self._settings.prefer_mps and self._machine == "arm64":
            devices.insert(0, "mps")

        last_error: Exception | None = None
        for device in devices:
            try:
                model = self._model_factory(
                    model=self._settings.asr_model,
                    vad_model=self._settings.vad_model,
                    vad_kwargs={"max_single_segment_time": 30_000},
                    device=device,
                    disable_update=True,
                    cache_dir=self._settings.model_cache_dir,
                )
            except Exception as exc:
                last_error = exc
                continue

            with self._state_lock:
                self._model = model
                self._state = "ready"
                self._device = device
                self._error = None
            return

        detail = self._sanitize_error(last_error)
        with self._state_lock:
            self._model = None
            self._state = "error"
            self._device = None
            self._error = detail
        raise ModelLoadError(detail)

    def transcribe(
        self,
        audio: PreparedAudio,
        language: str | None = None,
    ) -> TranscriptionResult:
        with self._state_lock:
            if self._state != "ready" or self._model is None:
                raise ModelNotReadyError()
            model = self._model

        model_input = self._pcm_converter(audio.pcm)
        with self._inference_lock:
            raw_result = model.generate(
                input=model_input,
                fs=audio.sample_rate,
                language=language or self._settings.language,
                use_itn=True,
            )

        if not raw_result:
            raise NoSpeechError()
        first = raw_result[0]
        text, tags = clean_sensevoice_text(str(first.get("text", "")))
        if not text:
            raise NoSpeechError()

        start_ms, end_ms = self._extract_bounds(first, audio.duration_ms)
        detected_language = normalize_language(tags[0]) if tags else normalize_language(
            language or self._settings.language
        )
        return TranscriptionResult(
            text=text,
            language=detected_language,
            start_ms=start_ms,
            end_ms=end_ms,
            audio_duration_ms=audio.duration_ms,
            tags=tags,
        )

    @staticmethod
    def _extract_bounds(result: dict[str, Any], duration_ms: int) -> tuple[int, int]:
        sentence_info = result.get("sentence_info")
        if isinstance(sentence_info, list) and sentence_info:
            starts = [item.get("start") for item in sentence_info if isinstance(item, dict)]
            ends = [item.get("end") for item in sentence_info if isinstance(item, dict)]
            valid_starts = [value for value in starts if isinstance(value, (int, float))]
            valid_ends = [value for value in ends if isinstance(value, (int, float))]
            if valid_starts and valid_ends:
                return max(0, round(min(valid_starts))), min(
                    duration_ms, round(max(valid_ends))
                )

        timestamps = result.get("timestamp")
        if isinstance(timestamps, list) and timestamps:
            pairs = [
                pair
                for pair in timestamps
                if isinstance(pair, (list, tuple))
                and len(pair) >= 2
                and isinstance(pair[0], (int, float))
                and isinstance(pair[1], (int, float))
            ]
            if pairs:
                return max(0, round(pairs[0][0])), min(
                    duration_ms, round(pairs[-1][1])
                )

        return 0, duration_ms

    @staticmethod
    def _sanitize_error(error: Exception | None) -> str:
        if error is None:
            return "unknown model initialization error"
        summary = str(error).splitlines()[0][:200]
        return f"{type(error).__name__}: {summary}"
