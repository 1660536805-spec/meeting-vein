#!/usr/bin/env python3
"""Load the local ASR model and optionally transcribe a local WAV fixture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.asr_runtime import AsrRuntime
from app.audio_gateway import AudioGateway
from app.config import Settings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wav", nargs="?", type=Path, help="local WAV file to transcribe")
    parser.add_argument("--fake", action="store_true", help="print deterministic readiness without loading weights")
    args = parser.parse_args()
    if args.fake:
        print(json.dumps({"ok": True, "state": "ready", "device": "fake"}))
        return 0
    if args.wav is None or not args.wav.is_file():
        parser.error("provide an existing WAV file, or use --fake")

    settings = Settings()
    runtime = AsrRuntime(settings)
    try:
        runtime.load()
        audio = AudioGateway(settings).prepare(args.wav.read_bytes())
        result = runtime.transcribe(audio)
    except Exception as error:
        print(json.dumps({"ok": False, "state": "error", "error": type(error).__name__}))
        return 1

    status = runtime.status()
    print(json.dumps({
        "ok": True,
        "state": status.state,
        "device": status.device,
        "language": result.language,
        "transcript_characters": len(result.text),
        "audio_duration_ms": result.audio_duration_ms,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
