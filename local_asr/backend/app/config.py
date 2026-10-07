from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration sourced from LOCAL_ASR_* environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="LOCAL_ASR_",
        env_file=".env",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=9000, ge=1, le=65_535)
    frontend_origins: tuple[str, ...] = (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    )

    asr_model: str = "iic/SenseVoiceSmall"
    streaming_model: str = "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"
    vad_model: str = "fsmn-vad"
    language: str = "auto"
    prefer_mps: bool = True
    model_cache_dir: Path = Field(
        default_factory=lambda: Path.home() / ".cache" / "modelscope"
    )
    streaming_cache_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[2] / "model_cache"
    )

    max_upload_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    max_audio_seconds: float = Field(default=120, gt=0)
    ffmpeg_path: str = "ffmpeg"

    # 流式静音端检：连续静音达到阈值即把当前累积文本切成一段（final），
    # 供前端逐段实时提交看板；长时间不停顿也会按上限强制切段。
    stream_silence_ms: int = Field(default=800, ge=0)
    stream_silence_rms: float = Field(default=0.01, gt=0)
    stream_min_segment_chars: int = Field(default=2, ge=1)
    stream_max_segment_ms: int = Field(default=8_000, gt=0)

    debug_save_audio: bool = False
    debug_audio_dir: Path = Field(
        default_factory=lambda: Path.home() / ".local" / "share" / "local-asr" / "debug-audio"
    )
