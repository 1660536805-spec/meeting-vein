import io
import math
import wave
from array import array


def make_wav(
    *,
    sample_rate: int = 16_000,
    channels: int = 1,
    seconds: float = 0.25,
) -> bytes:
    frame_count = int(sample_rate * seconds)
    samples = array("h")
    for index in range(frame_count):
        sample = int(6_000 * math.sin(2 * math.pi * 440 * index / sample_rate))
        samples.extend([sample] * channels)

    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(samples.tobytes())
    return output.getvalue()


def make_fake_riff() -> bytes:
    return b"RIFF\x08\x00\x00\x00WAVEfmt "
