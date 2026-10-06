# Live local transcription design

## Goal

While recording a meeting, show provisional text as the speaker talks. Aim for less than one second from captured speech to a visible update on this Mac, and measure actual latency; do not promise a hard bound before measurement. Pausing freezes the preview, resuming continues it, and stopping uses the existing SenseVoiceSmall result as the final text and the sole event sent to the board.

## Design

The browser captures the microphone once. MediaRecorder retains the complete recording for final transcription. An AudioWorklet taps the same stream; the browser resamples mono PCM to 16 kHz and sends 480 ms PCM16 frames through a Vite-proxied WebSocket to the local ASR process. The server gives each connection a private Paraformer streaming cache, processes frames in order, and returns incremental text. The browser appends those text pieces to a provisional transcript and displays measured audio-block-to-display latency. The final SenseVoice response replaces the provisional text. Streaming errors leave the complete recording and final transcription usable.

The streaming model loads in the background at server startup, separately from SenseVoiceSmall. A readiness field in the existing model-status response lets the UI indicate when live transcription is unavailable. A local-only origin check protects the WebSocket. Audio and provisional text are not persisted by the streaming path.

## Verification

Backend tests cover per-connection cache isolation, ordered PCM frame handling, errors, and finalization. Frontend tests cover capture chunking, pause/resume, provisional text, final replacement, and streaming failure fallback. Build and full test suites run. A real microphone test and latency measurement are required before claiming the one-second target has been met.
