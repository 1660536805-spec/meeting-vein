from unittest.mock import Mock

import pytest

from app.audio_gateway import AudioGateway, AudioInputError
from app.config import Settings
from tests.audio_fixtures import make_fake_riff, make_wav


@pytest.fixture
def gateway(tmp_path) -> AudioGateway:
    return AudioGateway(
        Settings(max_upload_bytes=1_000_000, max_audio_seconds=1.5),
        temp_root=tmp_path,
    )


def test_prepare_normalizes_stereo_48khz_wav_to_mono_16khz(gateway) -> None:
    audio = gateway.prepare(make_wav(sample_rate=48_000, channels=2, seconds=1.0))

    assert audio.sample_rate == 16_000
    assert len(audio.pcm) == 16_000
    assert audio.pcm.typecode == "f"
    assert 990 <= audio.duration_ms <= 1_010


@pytest.mark.parametrize("payload", [b"", b"not audio", make_fake_riff()])
def test_prepare_rejects_malformed_content(gateway, payload: bytes) -> None:
    with pytest.raises(AudioInputError, match="unsupported_audio"):
        gateway.prepare(payload)


def test_prepare_rejects_oversize_before_decoder_runs(tmp_path) -> None:
    runner = Mock()
    gateway = AudioGateway(
        Settings(max_upload_bytes=8, max_audio_seconds=1),
        runner=runner,
        temp_root=tmp_path,
    )

    with pytest.raises(AudioInputError) as error:
        gateway.prepare(b"x" * 9)

    assert error.value.code == "audio_too_large"
    runner.assert_not_called()


def test_prepare_rejects_audio_over_duration_limit(tmp_path) -> None:
    gateway = AudioGateway(
        Settings(max_upload_bytes=1_000_000, max_audio_seconds=0.5),
        temp_root=tmp_path,
    )

    with pytest.raises(AudioInputError) as error:
        gateway.prepare(make_wav(seconds=0.6))

    assert error.value.code == "audio_too_long"


def test_prepare_removes_temporary_directory_when_decoder_fails(tmp_path) -> None:
    gateway = AudioGateway(Settings(), temp_root=tmp_path)

    with pytest.raises(AudioInputError):
        gateway.prepare(b"not audio")

    assert list(tmp_path.iterdir()) == []


def test_debug_audio_is_saved_only_when_explicitly_enabled(tmp_path) -> None:
    payload = make_wav(seconds=0.2)
    debug_dir = tmp_path / "debug-audio"
    private_gateway = AudioGateway(
        Settings(debug_save_audio=True, debug_audio_dir=debug_dir),
        temp_root=tmp_path,
    )

    private_gateway.prepare(payload)

    saved = list(debug_dir.glob("*.audio"))
    assert len(saved) == 1
    assert saved[0].read_bytes() == payload
    assert saved[0].stat().st_mode & 0o777 == 0o600
    assert debug_dir.stat().st_mode & 0o777 == 0o700


def test_debug_audio_is_not_saved_by_default(tmp_path) -> None:
    gateway = AudioGateway(Settings(), temp_root=tmp_path)

    gateway.prepare(make_wav(seconds=0.2))

    assert not (tmp_path / ".local" / "share" / "local-asr" / "debug-audio").exists()
