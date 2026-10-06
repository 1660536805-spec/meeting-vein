import subprocess
import sys
import wave
import os
from array import array
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from secrets import token_hex
from tempfile import TemporaryDirectory
from typing import Any

from app.config import Settings


@dataclass(frozen=True, slots=True)
class PreparedAudio:
    pcm: array[float]
    sample_rate: int
    duration_ms: int


class AudioInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


Runner = Callable[..., Any]


class AudioGateway:
    """Validate an upload and normalize it to 16 kHz mono float PCM."""

    target_sample_rate = 16_000

    def __init__(
        self,
        settings: Settings,
        *,
        runner: Runner = subprocess.run,
        temp_root: Path | None = None,
    ) -> None:
        self.max_upload_bytes = settings.max_upload_bytes
        self.max_audio_seconds = settings.max_audio_seconds
        self.ffmpeg_path = settings.ffmpeg_path
        self._debug_save_audio = settings.debug_save_audio
        self._debug_audio_dir = settings.debug_audio_dir
        self._runner = runner
        self._temp_root = temp_root

    def prepare(self, data: bytes) -> PreparedAudio:
        if not data:
            raise AudioInputError("unsupported_audio", "音频文件为空或格式不受支持")
        if len(data) > self.max_upload_bytes:
            raise AudioInputError("audio_too_large", "音频文件超过大小限制")

        with TemporaryDirectory(dir=self._temp_root) as directory:
            temp_dir = Path(directory)
            source_path = temp_dir / "upload.bin"
            wav_path = temp_dir / "normalized.wav"
            source_path.write_bytes(data)
            self._decode(source_path, wav_path)
            prepared = self._read_pcm(wav_path)

        if prepared.duration_ms > round(self.max_audio_seconds * 1000):
            raise AudioInputError(
                "audio_too_long",
                f"单段音频不能超过 {self.max_audio_seconds:g} 秒",
            )
        if self._debug_save_audio:
            self._save_debug_audio(data)
        return prepared

    def _save_debug_audio(self, data: bytes) -> None:
        assert self._debug_audio_dir is not None
        self._debug_audio_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._debug_audio_dir, 0o700)
        path = self._debug_audio_dir / f"debug-{token_hex(12)}.audio"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as audio_file:
            audio_file.write(data)

    def _decode(self, source_path: Path, wav_path: Path) -> None:
        command: Sequence[str] = (
            self.ffmpeg_path,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(self.target_sample_rate),
            "-c:a",
            "pcm_s16le",
            str(wav_path),
        )
        try:
            self._runner(
                command,
                check=True,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise AudioInputError(
                "unsupported_audio",
                "无法解码音频，请上传 WAV、WebM、MP3 或 M4A 文件",
            ) from exc

    def _read_pcm(self, wav_path: Path) -> PreparedAudio:
        try:
            with wave.open(str(wav_path), "rb") as wav_file:
                channels = wav_file.getnchannels()
                sample_width = wav_file.getsampwidth()
                sample_rate = wav_file.getframerate()
                frame_count = wav_file.getnframes()
                frames = wav_file.readframes(frame_count)
        except (OSError, EOFError, wave.Error) as exc:
            raise AudioInputError("unsupported_audio", "转换后的音频无效") from exc

        if channels != 1 or sample_width != 2 or sample_rate != self.target_sample_rate:
            raise AudioInputError("unsupported_audio", "音频未能标准化为 16 kHz 单声道 PCM")

        integer_samples = array("h")
        integer_samples.frombytes(frames)
        if sys.byteorder != "little":
            integer_samples.byteswap()
        pcm = array("f", (sample / 32_768.0 for sample in integer_samples))
        duration_ms = round(frame_count * 1000 / sample_rate)
        return PreparedAudio(
            pcm=pcm,
            sample_rate=sample_rate,
            duration_ms=duration_ms,
        )
