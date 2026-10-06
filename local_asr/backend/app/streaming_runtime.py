"""Local streaming Paraformer model with one cache per microphone session."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.asr_runtime import ModelLoadError, ModelNotReadyError, RuntimeStatus
from app.config import Settings


def _model_factory(**kwargs: Any) -> Any:
    cache_dir = kwargs.pop("cache_dir")
    model_id = kwargs.pop("model")
    from funasr import AutoModel
    from modelscope.hub.snapshot_download import snapshot_download

    if Path(model_id).is_dir():
        model_path = model_id
    else:
        try:
            cached = snapshot_download(model_id, cache_dir=cache_dir, local_files_only=True)
        except Exception:
            cached = None
        model_path = cached if cached and (Path(cached) / "model.pt").is_file() else snapshot_download(
            model_id, cache_dir=cache_dir
        )
    return AutoModel(model=model_path, **kwargs)


class StreamingRuntime:
    """Share model weights, while the caller owns the mutable per-session cache."""

    def __init__(
        self,
        settings: Settings,
        *,
        model_factory: Callable[..., Any] = _model_factory,
    ) -> None:
        self._settings = settings
        self._factory = model_factory
        self._model: Any = None
        self._state = "not_loaded"
        self._error: str | None = None
        self._lock = threading.Lock()
        self._inference_lock = threading.Lock()

    def status(self) -> RuntimeStatus:
        with self._lock:
            return RuntimeStatus(self._state, "cpu" if self._state == "ready" else None, self._error)

    def load(self) -> None:
        with self._lock:
            if self._state in ("ready", "loading"):
                return
            self._state = "loading"
        try:
            model = self._factory(
                model=self._settings.streaming_model,
                device="cpu",
                disable_update=True,
                cache_dir=self._settings.streaming_cache_dir,
            )
            with self._lock:
                self._model = model
            warmup_cache: dict[str, Any] = {}
            for _ in range(2):
                self.transcribe(bytes(15_360), warmup_cache)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
            with self._lock:
                self._model = None
                self._error = detail
                self._state = "error"
            raise ModelLoadError(detail) from exc
        with self._lock:
            self._error = None
            self._state = "ready"

    def transcribe(self, frame: bytes, cache: dict[str, Any]) -> str:
        with self._lock:
            model = self._model
        if model is None:
            raise ModelNotReadyError()
        if len(frame) != 15_360:
            raise ValueError("stream frame must be 480 ms of 16 kHz mono PCM16")

        import numpy as np

        samples = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32_768.0
        with self._inference_lock:
            result = model.generate(
                input=samples,
                fs=16_000,
                cache=cache,
                is_final=False,
                chunk_size=[0, 8, 4],
                encoder_chunk_look_back=4,
                decoder_chunk_look_back=1,
                batch_size=1,
            )
        if not result:
            return ""
        return str(result[0].get("text", ""))
