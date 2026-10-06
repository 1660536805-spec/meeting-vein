from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApiErrorDetail(ContractModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ApiError(ContractModel):
    ok: Literal[False] = False
    error: ApiErrorDetail


class TranscriptionResult(ContractModel):
    text: str
    language: str = "auto"
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    audio_duration_ms: int | None = Field(default=None, ge=0)
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_time_range(self) -> "TranscriptionResult":
        if self.start_ms is not None and self.end_ms is not None:
            if self.end_ms < self.start_ms:
                raise ValueError("end_ms must not be before start_ms")
        return self


class Speaker(ContractModel):
    speaker_ref: str = "local:user"
    display_name: str = "本地发言人"
    is_resolved: bool = False


class NormUtterance(ContractModel):
    utterance_id: str = Field(pattern=r"^utt_local_")
    meeting_id: str = Field(min_length=1)
    session_id: str | None = None
    seq: int = Field(ge=0)
    speaker: Speaker = Field(default_factory=Speaker)
    text: str = Field(min_length=1)
    language: str = Field(min_length=1)
    start_offset_ms: int = Field(ge=0)
    end_offset_ms: int = Field(ge=0)
    received_at_ms: int = Field(ge=0)
    is_final: bool = True
    is_partial: bool = False
    source: Literal["local_sensevoice"] = "local_sensevoice"

    @model_validator(mode="after")
    def validate_event_state(self) -> "NormUtterance":
        if self.is_final == self.is_partial:
            raise ValueError("exactly one of is_final and is_partial must be true")
        if self.end_offset_ms < self.start_offset_ms:
            raise ValueError("end_offset_ms must not be before start_offset_ms")
        return self
