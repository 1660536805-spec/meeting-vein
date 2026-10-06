#!/usr/bin/env python3
"""Measure local streaming model inference using its bundled speech sample."""

from __future__ import annotations

import json
import math
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import Settings
from app.streaming_runtime import StreamingRuntime


def main() -> int:
    settings = Settings()
    sample = (
        settings.streaming_cache_dir
        / "models/iic--speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"
        / "snapshots/master/example/asr_example.wav"
    )
    if not sample.exists():
        print(json.dumps({"ok": False, "error": "streaming model sample not downloaded"}))
        return 1

    runtime = StreamingRuntime(settings)
    runtime.load()
    with wave.open(str(sample), "rb") as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (16_000, 1, 2):
            raise ValueError("sample must be 16 kHz mono PCM16")
        frames = source.readframes(source.getnframes())

    frame_bytes = 15_360
    cache: dict[str, object] = {}
    inference_ms: list[float] = []
    transcript = ""
    for offset in range(0, len(frames), frame_bytes):
        frame = frames[offset:offset + frame_bytes].ljust(frame_bytes, b"\0")
        started = time.perf_counter()
        transcript += runtime.transcribe(frame, cache)
        inference_ms.append(round((time.perf_counter() - started) * 1000, 1))

    ordered = sorted(inference_ms)
    p95 = ordered[math.ceil(0.95 * len(ordered)) - 1]
    print(json.dumps({
        "ok": True,
        "sample_duration_ms": round(len(frames) / 32),
        "chunk_ms": 480,
        "inference_ms": inference_ms,
        "p95_inference_ms": p95,
        "estimated_capture_to_result_p95_ms": 480 + p95,
        "transcript_characters": len(transcript),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
