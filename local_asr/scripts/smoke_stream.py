#!/usr/bin/env python3
"""Exercise the live WebSocket through the frontend proxy with sample speech."""

from __future__ import annotations

import json
import math
import sys
import time
import wave
from pathlib import Path

from websockets.sync.client import connect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.config import Settings


def main() -> int:
    sample = (
        Settings().streaming_cache_dir
        / "models/iic--speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"
        / "snapshots/master/example/asr_example.wav"
    )
    with wave.open(str(sample), "rb") as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (16_000, 1, 2):
            raise ValueError("sample must be 16 kHz mono PCM16")
        audio = source.readframes(source.getnframes())

    frame_bytes = 15_360
    results: list[float] = []
    characters = 0
    with connect(
        "ws://127.0.0.1:5173/asr/v1/audio/stream",
        origin="http://127.0.0.1:5173",
        open_timeout=5,
    ) as socket:
        assert json.loads(socket.recv())["type"] == "ready"
        started = time.perf_counter()
        for seq, offset in enumerate(range(0, len(audio), frame_bytes), start=1):
            frame_started = started + (seq - 1) * 0.48
            time.sleep(max(0, frame_started + 0.48 - time.perf_counter()))
            frame = audio[offset:offset + frame_bytes].ljust(frame_bytes, b"\0")
            socket.send(frame)
            reply = json.loads(socket.recv())
            if reply.get("type") != "partial" or reply.get("seq") != seq:
                raise RuntimeError(f"unexpected stream response: {reply.get('type')}")
            if reply["text"]:
                characters += len(reply["text"])
                results.append(round((time.perf_counter() - frame_started) * 1000, 1))

    ordered = sorted(results)
    print(json.dumps({
        "ok": True,
        "partial_chunks": len(results),
        "transcript_characters": characters,
        "capture_to_response_ms": results,
        "p95_capture_to_response_ms": ordered[math.ceil(0.95 * len(ordered)) - 1] if ordered else None,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
