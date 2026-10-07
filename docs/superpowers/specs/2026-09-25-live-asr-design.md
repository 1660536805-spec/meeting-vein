# Live local transcription design

## Goal

While recording a meeting, show provisional text as the speaker talks. Aim for less than one second from captured speech to a visible update on this Mac, and measure actual latency; do not promise a hard bound before measurement. Partial text is display-only. A final segment emitted at a silence boundary is the normal event sent to the board; stopping does not automatically make a complete-recording transcript a second source of board events.

## Design

The browser captures the microphone once. MediaRecorder retains the complete recording as a recovery source. An AudioWorklet taps the same stream; the browser resamples mono PCM to 16 kHz and sends 480 ms PCM16 frames through a Vite-proxied WebSocket to the local ASR process. The server gives each connection a private Paraformer streaming cache, processes frames in order, and returns incremental text. The browser appends those text pieces to a provisional transcript and displays measured audio-block-to-display latency. Each silence-delimited final segment has a stable ID and is submitted once; successful segments are not resubmitted from the complete SenseVoice result. SenseVoice is a fallback only when no segment was successfully submitted, or when the user explicitly requests correction. Failed final segments remain in a durable browser queue for retry/export. Streaming errors leave the complete recording usable.

The streaming model loads in the background at server startup, separately from SenseVoiceSmall. A readiness field in the existing model-status response lets the UI indicate when live transcription is unavailable. A local-only origin check protects the WebSocket. Audio and provisional text are not persisted by the streaming path. Store B does persist submitted utterance text. Target ingest states (`accepted`, `processing`, `committed`, `failed`) and recovery rules are defined in [`../../../doc/contracts/utterance-state.md`](../../../doc/contracts/utterance-state.md); current endpoint behavior has not yet implemented that contract.

## Verification

Backend tests cover per-connection cache isolation, ordered PCM frame handling, errors, and finalization. Frontend tests cover capture chunking, pause/resume, provisional text, segment submission, and streaming failure fallback. Recovery tests must also prove segment idempotency and prevent full-recording fallback from duplicating accepted segments. Build and full test suites run. A real microphone test and latency measurement are required before claiming the one-second target has been met.
