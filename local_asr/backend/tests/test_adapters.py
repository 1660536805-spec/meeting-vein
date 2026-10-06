import pytest

from app.adapters import NoSpeechError, clean_sensevoice_text, to_norm_utterance
from app.schemas import TranscriptionResult


def test_clean_sensevoice_text_separates_leading_tags_from_transcript() -> None:
    text, tags = clean_sensevoice_text("<|zh|><|NEUTRAL|><|Speech|>今天开会")

    assert text == "今天开会"
    assert tags == ["zh", "NEUTRAL", "Speech"]


def test_clean_sensevoice_text_preserves_angle_brackets_inside_transcript() -> None:
    text, tags = clean_sensevoice_text("<|zh|>讨论 A < B 与 C > B")

    assert text == "讨论 A < B 与 C > B"
    assert tags == ["zh"]


def test_clean_sensevoice_text_removes_metadata_between_segments() -> None:
    raw = (
        "<|zh|><|NEUTRAL|><|Speech|>第一句。 "
        "<|zh|><|NEUTRAL|><|BGM|><|withitn|>第二句。"
    )
    text, tags = clean_sensevoice_text(raw)
    assert text == "第一句。 第二句。"
    assert tags[0] == "zh"


def test_adapter_builds_stable_final_event() -> None:
    event = to_norm_utterance(
        TranscriptionResult(text="今天开会", language="zh", start_ms=120, end_ms=980),
        meeting_id="mtg_demo",
        seq=7,
        received_at_ms=1234,
        id_factory=lambda: "utt_local_fixed",
    )

    assert event.model_dump() == {
        "utterance_id": "utt_local_fixed",
        "meeting_id": "mtg_demo",
        "session_id": None,
        "seq": 7,
        "speaker": {
            "speaker_ref": "local:user",
            "display_name": "本地发言人",
            "is_resolved": False,
        },
        "text": "今天开会",
        "language": "zh",
        "start_offset_ms": 120,
        "end_offset_ms": 980,
        "received_at_ms": 1234,
        "is_final": True,
        "is_partial": False,
        "source": "local_sensevoice",
    }


def test_adapter_refuses_empty_transcript() -> None:
    with pytest.raises(NoSpeechError):
        to_norm_utterance(
            TranscriptionResult(text="  ", language="zh", start_ms=0, end_ms=100),
            meeting_id="mtg_demo",
            seq=1,
            received_at_ms=0,
        )


def test_adapter_uses_audio_bounds_when_model_timestamps_are_missing() -> None:
    event = to_norm_utterance(
        TranscriptionResult(
            text="测试",
            language="zh",
            start_ms=None,
            end_ms=None,
            audio_duration_ms=640,
        ),
        meeting_id="mtg_demo",
        seq=1,
        received_at_ms=0,
        id_factory=lambda: "utt_local_fixed",
    )

    assert (event.start_offset_ms, event.end_offset_ms) == (0, 640)


def test_adapter_generates_local_prefixed_id_by_default() -> None:
    event = to_norm_utterance(
        TranscriptionResult(text="测试", language="Chinese", audio_duration_ms=100),
        meeting_id="mtg_demo",
        seq=1,
        received_at_ms=0,
    )

    assert event.utterance_id.startswith("utt_local_")
    assert event.language == "zh"
