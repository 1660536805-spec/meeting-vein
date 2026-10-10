import asyncio
import json
import logging
import re
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Annotated, Any
from uuid import uuid4

from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.adapters import NoSpeechError, to_norm_utterance
from app.asr_runtime import AsrRuntime, ModelLoadError, ModelNotReadyError
from app.audio_gateway import AudioGateway, AudioInputError
from app.config import Settings
from app.schemas import ApiError, ApiErrorDetail
from app.streaming_runtime import StreamingRuntime


logger = logging.getLogger("local_asr")


_FRAME_MS = 480
_FRAME_BYTES = 15_360
_STREAM_SESSION_TTL_S = 120
_STREAM_SESSION_LIMIT = 128


@dataclass
class StreamingSession:
    session_id: str
    generation: str
    session_started_ms: int
    segment_started_ms: int
    last_active: float
    cache: dict[str, Any] = field(default_factory=dict)
    seq: int = 0
    segment_seq: int = 0
    parts: list[str] = field(default_factory=list)
    silent_ms: int = 0
    pending_segments: list[dict[str, Any]] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _frame_rms(frame: bytes) -> float:
    """归一化后计算一帧 PCM16 的能量，用于静音端检。"""
    import numpy as np

    samples = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32_768.0
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(float(np.mean(samples * samples))))


def _join_deltas(parts: list[str]) -> str:
    """拼接流式增量文本，中英混排时在英文词之间补空格。"""
    text = ""
    for part in parts:
        if not part:
            continue
        needs_space = (
            text
            and text[-1].isascii()
            and text[-1].isalnum()
            and part[0].isascii()
            and part[0].isalnum()
        )
        text += (" " if needs_space else "") + part
    return text


_ERROR_STATUS = {
    "audio_too_large": 413,
    "audio_too_long": 422,
    "model_load_failed": 503,
    "model_not_ready": 503,
    "no_speech": 422,
    "unsupported_audio": 415,
}


def _error_response(code: str, message: str) -> JSONResponse:
    body = ApiError(error=ApiErrorDetail(code=code, message=message))
    return JSONResponse(
        status_code=_ERROR_STATUS.get(code, 500),
        content=body.model_dump(mode="json"),
    )


def create_app(
    settings: Settings | None = None,
    runtime: AsrRuntime | Any | None = None,
    gateway: AudioGateway | Any | None = None,
    streaming_runtime: StreamingRuntime | Any | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings()
    resolved_runtime = runtime or AsrRuntime(resolved_settings)
    resolved_gateway = gateway or AudioGateway(resolved_settings)
    resolved_streaming = streaming_runtime or (StreamingRuntime(resolved_settings) if runtime is None else None)
    stream_sessions: dict[str, StreamingSession] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        status = resolved_runtime.status()
        if status.state == "not_loaded" and getattr(resolved_settings, "enable_models", True):
            threading.Thread(
                target=_load_runtime,
                args=(resolved_runtime,),
                name="local-asr-model-loader",
                daemon=True,
            ).start()
        if (
            resolved_streaming is not None
            and resolved_streaming.status().state == "not_loaded"
            and getattr(resolved_settings, "enable_models", True)
        ):
            threading.Thread(
                target=_load_runtime,
                args=(resolved_streaming,),
                name="local-streaming-model-loader",
                daemon=True,
            ).start()
        yield

    application = FastAPI(
        title="Local ASR Prototype",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.runtime = resolved_runtime
    application.state.gateway = resolved_gateway
    application.state.streaming_runtime = resolved_streaming
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.frontend_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID"],
    )

    @application.middleware("http")
    async def request_metrics(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid4().hex
        started = time.monotonic()
        response = await call_next(request)
        duration_ms = round((time.monotonic() - started) * 1000)
        response.headers["x-request-id"] = request_id
        logger.info(
            "request method=%s path=%s status=%s duration_ms=%s request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
        )
        return response

    @application.get("/health")
    def health() -> dict[str, object]:
        status = resolved_runtime.status()
        return {"ok": True, "model_status": status.state}

    @application.get("/models/status")
    def models_status() -> dict[str, object]:
        status = resolved_runtime.status()
        live_status = resolved_streaming.status() if resolved_streaming is not None else None
        return {
            "ok": True,
            "model": resolved_settings.asr_model,
            "vad_model": resolved_settings.vad_model,
            "state": status.state,
            "device": status.device,
            "error": status.error,
            "streaming_state": live_status.state if live_status else "not_loaded",
            "streaming_error": live_status.error if live_status else None,
        }

    @application.websocket("/v1/audio/stream")
    async def stream_audio(websocket: WebSocket) -> None:
        if websocket.headers.get("origin") not in resolved_settings.frontend_origins:
            await websocket.close(code=1008)
            return
        session_id = websocket.query_params.get("session_id") or uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}", session_id):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        if resolved_streaming is None or resolved_streaming.status().state != "ready":
            await websocket.send_json({"type": "error", "message": "流式语音模型尚未就绪"})
            await websocket.close(code=1013)
            return

        now = time.monotonic()
        for key, value in tuple(stream_sessions.items()):
            if now - value.last_active > _STREAM_SESSION_TTL_S and not value.lock.locked():
                stream_sessions.pop(key, None)
        session = stream_sessions.get(session_id)
        if session is None:
            if len(stream_sessions) >= _STREAM_SESSION_LIMIT:
                idle = sorted(
                    (item for item in stream_sessions.values() if not item.lock.locked()),
                    key=lambda item: item.last_active,
                )
                while idle and len(stream_sessions) >= _STREAM_SESSION_LIMIT:
                    stream_sessions.pop(idle.pop(0).session_id, None)
            if len(stream_sessions) >= _STREAM_SESSION_LIMIT:
                await websocket.send_json({"type": "error", "message": "实时识别会话已满，请稍后重试"})
                await websocket.close(code=1013)
                return
            started_ms = int(time.time() * 1000)
            session = StreamingSession(session_id, uuid4().hex, started_ms, started_ms, now)
            stream_sessions[session_id] = session

        def flush_segment(now_ms: int) -> dict[str, Any] | None:
            """把当前累积文本切成一段：重置模型缓存与会话状态，返回 final 消息。"""
            text = _join_deltas(session.parts).strip()
            start_ms = session.segment_started_ms
            session.parts = []
            session.silent_ms = 0
            session.segment_started_ms = now_ms
            session.cache.clear()
            if not text:
                return None
            session.segment_seq += 1
            return {
                "type": "final",
                "seq": session.seq,
                "segment_seq": session.segment_seq,
                "segment_id": f"utt_local_stream_{session.session_id}_{session.generation}_{session.segment_seq}",
                "text": text,
                "language": resolved_settings.language,
                "start_offset_ms": max(0, start_ms - session.session_started_ms),
                "end_offset_ms": max(0, now_ms - session.session_started_ms),
            }

        async with session.lock:
            session.last_active = time.monotonic()
            try:
                await websocket.send_json({
                    "type": "ready", "seq": session.seq,
                    "generation": session.generation,
                    "partial_text": _join_deltas(session.parts),
                })
                for event in session.pending_segments:
                    await websocket.send_json(event)
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        return
                    frame = message.get("bytes")
                    if frame is None:
                        raw_control = message.get("text")
                        if raw_control is None:
                            continue
                        try:
                            control = json.loads(raw_control)
                        except (TypeError, ValueError):
                            control = {}
                        if not isinstance(control, dict):
                            continue
                        if control.get("type") == "ack" and isinstance(control.get("segment_id"), str):
                            session.pending_segments = [
                                event for event in session.pending_segments
                                if event.get("segment_id") != control["segment_id"]
                            ]
                            session.last_active = time.monotonic()
                        elif control.get("type") == "flush":
                            event = flush_segment(int(time.time() * 1000))
                            if event is not None:
                                session.pending_segments.append(event)
                                await websocket.send_json(event)
                            await websocket.send_json({"type": "flushed"})
                        continue
                    if len(frame) != _FRAME_BYTES:
                        await websocket.send_json({"type": "error", "message": "无效的音频帧"})
                        await websocket.close(code=1003)
                        return
                    session.seq += 1
                    session.last_active = time.monotonic()
                    rms = _frame_rms(frame)
                    try:
                        text = await asyncio.to_thread(resolved_streaming.transcribe, frame, session.cache)
                    except Exception:
                        logger.exception("streaming inference failed")
                        await websocket.send_json({"type": "error", "message": "实时识别失败，停止后仍可识别完整录音"})
                        await websocket.close(code=1011)
                        return
                    text = text.strip()
                    now_ms = int(time.time() * 1000)
                    if text:
                        session.parts.append(text)
                        session.silent_ms = 0
                    elif rms < resolved_settings.stream_silence_rms:
                        session.silent_ms += _FRAME_MS
                    else:
                        session.silent_ms = 0

                    punctuated = bool(session.parts) and len(_join_deltas(session.parts).strip()) >= resolved_settings.stream_min_segment_chars
                    if punctuated and (
                        session.silent_ms >= resolved_settings.stream_silence_ms
                        or now_ms - session.segment_started_ms >= resolved_settings.stream_max_segment_ms
                    ):
                        event = flush_segment(now_ms)
                        if event is not None:
                            session.pending_segments.append(event)
                            await websocket.send_json(event)
                            continue
                    await websocket.send_json({"type": "partial", "seq": session.seq, "text": text})
            except WebSocketDisconnect:
                return
            except RuntimeError:
                # The socket may disappear while an ASR inference task is running;
                # the shared session retains its processed frame and pending final.
                return
            finally:
                session.last_active = time.monotonic()

    @application.post("/v1/audio/transcriptions")
    async def transcribe(
        file: Annotated[UploadFile, File()],
        meeting_id: Annotated[str, Form()] = "mtg_demo",
        seq: Annotated[int, Form(ge=0)] = 1,
        language: Annotated[str, Form()] = "auto",
    ) -> JSONResponse:
        runtime_status = resolved_runtime.status()
        if runtime_status.state != "ready":
            if runtime_status.state == "error":
                return _error_response("model_load_failed", "本地语音模型加载失败")
            return _error_response(
                ModelNotReadyError.code,
                ModelNotReadyError.message,
            )

        try:
            data = await file.read(resolved_settings.max_upload_bytes + 1)
        finally:
            await file.close()
        if len(data) > resolved_settings.max_upload_bytes:
            return _error_response("audio_too_large", "音频文件超过大小限制")

        try:
            prepared = resolved_gateway.prepare(data)
            result = resolved_runtime.transcribe(prepared, language=language)
            event = to_norm_utterance(
                result,
                meeting_id=meeting_id,
                seq=seq,
                received_at_ms=int(time.time() * 1000),
            )
        except AudioInputError as error:
            return _error_response(error.code, error.message)
        except NoSpeechError as error:
            return _error_response(error.code, error.message)
        except ModelNotReadyError as error:
            return _error_response(error.code, error.message)
        except ModelLoadError as error:
            return _error_response(error.code, error.message)

        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "transcription": result.model_dump(mode="json"),
                "event": event.model_dump(mode="json"),
            },
        )

    return application


def _load_runtime(runtime: AsrRuntime | Any) -> None:
    try:
        runtime.load()
    except ModelLoadError:
        logger.exception("model initialization failed")


app = create_app()
