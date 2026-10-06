import re
from collections.abc import Callable
from uuid import uuid4

from app.schemas import NormUtterance, Speaker, TranscriptionResult


_SENSEVOICE_TAG = re.compile(r"<\|([^|<>]+)\|>")
_LANGUAGE_CODES = {
    "auto": "auto",
    "chinese": "zh",
    "zh": "zh",
    "yue": "yue",
    "cantonese": "yue",
    "english": "en",
    "en": "en",
    "japanese": "ja",
    "ja": "ja",
    "korean": "ko",
    "ko": "ko",
}


class NoSpeechError(ValueError):
    code = "no_speech"
    message = "未检测到有效语音"

    def __init__(self) -> None:
        super().__init__(self.message)


def clean_sensevoice_text(raw: str) -> tuple[str, list[str]]:
    """Remove metadata from every SenseVoice segment in a long recording."""

    tags = _SENSEVOICE_TAG.findall(raw)
    return _SENSEVOICE_TAG.sub("", raw).strip(), tags


def normalize_language(language: str) -> str:
    normalized = language.strip().casefold()
    return _LANGUAGE_CODES.get(normalized, normalized or "auto")


def _new_utterance_id() -> str:
    return f"utt_local_{uuid4().hex}"


def to_norm_utterance(
    result: TranscriptionResult,
    *,
    meeting_id: str,
    seq: int,
    received_at_ms: int,
    id_factory: Callable[[], str] = _new_utterance_id,
) -> NormUtterance:
    text = result.text.strip()
    if not text:
        raise NoSpeechError()

    start_ms = result.start_ms if result.start_ms is not None else 0
    if result.end_ms is not None:
        end_ms = result.end_ms
    elif result.audio_duration_ms is not None:
        end_ms = result.audio_duration_ms
    else:
        end_ms = start_ms

    return NormUtterance(
        utterance_id=id_factory(),
        meeting_id=meeting_id,
        session_id=None,
        seq=seq,
        speaker=Speaker(),
        text=text,
        language=normalize_language(result.language),
        start_offset_ms=start_ms,
        end_offset_ms=max(start_ms, end_ms),
        received_at_ms=received_at_ms,
        is_final=True,
        is_partial=False,
        source="local_sensevoice",
    )
