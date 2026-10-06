import pytest
from pydantic import ValidationError

from app.config import Settings
from app.schemas import NormUtterance, Speaker, TranscriptionResult


def make_event(**overrides: object) -> NormUtterance:
    values = {
        "utterance_id": "utt_local_1",
        "meeting_id": "mtg_demo",
        "seq": 1,
        "speaker": Speaker(),
        "text": "测试",
        "language": "zh",
        "start_offset_ms": 0,
        "end_offset_ms": 500,
        "received_at_ms": 0,
        "is_final": True,
        "is_partial": False,
        "source": "local_sensevoice",
    }
    values.update(overrides)
    return NormUtterance(**values)


def test_settings_are_loopback_only_by_default() -> None:
    settings = Settings()
    assert (settings.host, settings.port) == ("127.0.0.1", 9000)
    assert settings.max_audio_seconds == 120


def test_norm_utterance_rejects_partial_final_conflict() -> None:
    with pytest.raises(ValidationError):
        make_event(is_final=True, is_partial=True)


def test_norm_utterance_rejects_reversed_offsets() -> None:
    with pytest.raises(ValidationError):
        make_event(start_offset_ms=501, end_offset_ms=500)


def test_transcription_result_uses_audio_duration_when_timestamps_are_absent() -> None:
    result = TranscriptionResult(
        text="测试",
        language="zh",
        start_ms=None,
        end_ms=None,
        audio_duration_ms=640,
    )
    assert result.audio_duration_ms == 640
