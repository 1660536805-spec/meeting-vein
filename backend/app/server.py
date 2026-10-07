"""FastAPI 服务入口（M3 后端服务 + WS 总线 + Agent 工具层）。

运行（需先 pip install -r requirements.txt）：
    ..\\.venv\\Scripts\\python.exe -m uvicorn app.server:app --reload --port 8000

端点：
- GET  /api/board             → 返回关系图 cells（前端 fromJSON 渲染）
- GET  /api/metadata?ids=     → 按 meta_id 反查原始论据
- GET  /api/cursor/config     → 下发采集参数（THROTTLE_MS 等）
- POST /api/cursor/config     → 接收前端上报/调优
- WS   /ws                    → 上行 asr.event / cursor.event；下行 mascot_state / board.update
- POST /api/cli/push        → CLI 调试口：一行键入文本 → from_cli_text 归一化 → 驱动编排（无需真实 ASR）

注意：本文件依赖 fastapi/uvicorn/websockets，当前沙箱若未联网安装则无法 import 运行；
代码结构与接口契约已就绪，联网后直接启用（DevPlan §4）。
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import time
import uuid
import json as jsonlib
from urllib.error import URLError
from urllib.request import urlopen
from collections import OrderedDict
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Annotated, Optional

from .ws.bus import EventBus
from .storage import MEETING_ID_PATTERN, StoreA, StoreB, VersionConflict, is_valid_meeting_id
from .llm import build_llm, llm_metrics, MockLLM, OpenAIClient
from .orchestrator import BoardAgent
from . import config, prompts
from .adapters import from_tencent_asr_push, from_x6_event, from_cli_text, from_stored_record
from .utterance_ingest import IncomingUtterance, UtteranceIngestor, metadata_id
from .models import GraphOp, GraphUpdateOp, stable_hash
from .tools.graph_tools import GraphTools
from .tools.metadata_tools import MetadataTools
from .minutes import parse_agenda, render_minutes, review_before_close
from .errors import safe_error_code


logging.basicConfig(level=os.getenv("AMO_LOG_LEVEL", "INFO").upper(),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("amo.server")
STARTED_AT = time.monotonic()


def _storage_root() -> str:
    """解析存储根目录：相对路径一律相对 backend/ 解析（与 config.storage_root 单一来源）。"""
    root = config.CONFIG.storage_root
    if not os.path.isabs(root):
        root = os.path.join(os.path.dirname(__file__), "..", root)
    return os.path.abspath(root)


ROOT = _storage_root()
bus = EventBus()
store_a = StoreA(ROOT)
store_b = StoreB(ROOT)
agent = BoardAgent(store_a, store_b, build_llm())
graph_tools = GraphTools(store_a, store_b)
metadata_tools = MetadataTools(store_a, store_b)
_cursor_buffers: dict[str, list] = {}

# 允许前端调优的光标采集参数白名单（字段 → (类型, 下界, 上界)）；其余配置项一律拒绝覆写。
_MUTABLE_CURSOR_CONFIG = {
    "throttle_ms": (int, 0, 60000),
    "hover_settle_ms": (int, 0, 60000),
    "drag_px_threshold": (int, 0, 1000),
    "view_mode_sample_rate": (float, 0.0, 1.0),
    "cursor_independent_trigger": (bool, None, None),
    "cursor_fusion_window_ms": (int, 100, 10000),
}


def _validated(value, spec) -> tuple:
    """按白名单规格校验单个值，返回 (ok, coerced)。"""
    typ, lo, hi = spec
    if typ is bool:
        return (isinstance(value, bool), value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return (False, None)
    if typ is int:
        iv = int(value)
        return (lo <= iv <= hi, iv)
    fv = float(value)
    return (lo <= fv <= hi, fv)


def require_token(authorization: Optional[str] = Header(default=None)) -> None:
    """写接口可选鉴权：置 AMO_API_TOKEN 后须带 Authorization: Bearer <token>。

    未配置 token 时放行（纯本机开发，零配置可用）；配置后即强制校验，防误暴露的写接口被滥用。
    """
    token = config.CONFIG.api_token
    if not token:
        return
    presented = ""
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()
    if presented != token:
        raise HTTPException(status_code=401, detail="invalid or missing api token")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 启动时迁移历史看板坐标（旧数据来自伪随机撒点，存在重叠）。
    try:
        store_a.relayout_all()
    except Exception as e:
        log.warning("[startup] relayout_all failed (%s)", safe_error_code(e))
    try:
        recovered = await utterance_ingestor.recover_incomplete()
        if recovered:
            log.info("[startup] recovered %d incomplete utterances", recovered)
    except Exception as e:
        log.warning("[startup] utterance recovery failed (%s)", safe_error_code(e))

    async def _flusher():                # 兜底：周期落盘节流中的脏数据，保证最终一致
        while True:
            await asyncio.sleep(1.0)
            store_a.flush_all()
            store_b.flush(force=True)

    async def _pending_poller():         # P0①：失败批次后台补偿重驱动
        while True:
            await asyncio.sleep(max(1.0, config.CONFIG.pending_retry_interval_s))
            try:
                res = await _retry_pending_batches()
                if res.get("recovered") or res.get("still_failing"):
                    log.info("[pending] poll retry: %s", res)
            except Exception as e:
                    log.warning("[pending] poll retry failed (%s)", safe_error_code(e))

    task = asyncio.create_task(_flusher())
    poller = asyncio.create_task(_pending_poller())
    try:
        yield
    finally:
        task.cancel()
        poller.cancel()
        try:                             # 关停前冲刷实时合批器中未驱动的语句
            await utterance_ingestor.flush_all()
        except Exception as e:
            log.warning("[shutdown] batcher flush failed (%s)", safe_error_code(e))
        store_a.flush_all()              # 关停前强制落盘
        store_b.flush(force=True)


app = FastAPI(title="AI Meeting Organizer Board Server", lifespan=lifespan)
# 仅放行本机来源（Vite 开发端口可能变动，故用正则匹配 127.0.0.1/localhost 的任意端口）。
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(127\.0\.0\.1|localhost)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)


def _probe_asr_status() -> dict:
    """Probe the actual local ASR worker; never infer readiness from configuration."""
    url = config.CONFIG.local_asr_base_url.rstrip("/") + "/models/status"
    try:
        with urlopen(url, timeout=0.6) as response:
            payload = jsonlib.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid ASR status")
        final_state = payload.get("state") or "unavailable"
        streaming_state = payload.get("streaming_state") or "unavailable"
        return {
            "streaming": {"state": streaming_state, "error": payload.get("streaming_error")},
            "final": {"state": final_state, "error": payload.get("error"),
                      "device": payload.get("device")},
        }
    except (OSError, URLError, TimeoutError, ValueError, jsonlib.JSONDecodeError):
        return {"streaming": {"state": "unavailable", "error": None},
                "final": {"state": "unavailable", "error": None}}


def _storage_state(root: str) -> str:
    return "ready" if os.path.isdir(root) and os.access(root, os.R_OK | os.W_OK) else "unavailable"


@app.get("/api/status")
async def product_status():
    llm_instance = agent.llm
    llm_type = ("mock" if isinstance(llm_instance, MockLLM)
                else "openai_compatible" if isinstance(llm_instance, OpenAIClient) else "custom")
    llm_mode = "mock" if llm_type == "mock" else "real" if llm_type == "openai_compatible" else "unknown"
    pending_batches = store_b.list_by_kind("pending_batch")
    pending_retries = len(pending_batches)
    pending_by_meeting: dict[str, int] = {}
    for batch in pending_batches:
        mid = batch.get("meeting_id")
        if mid:
            pending_by_meeting[mid] = pending_by_meeting.get(mid, 0) + 1
    return {"llm_mode": llm_mode,
            "llm_instance": {"type": llm_type, "model": getattr(llm_instance, "model", None)},
            "asr": _probe_asr_status(),
            "persistence": {"store_a": _storage_state(store_a.root),
                            "store_b": _storage_state(store_b.root),
                            "pending_retries": pending_retries},
            "uptime_s": round(time.monotonic() - STARTED_AT, 1),
            "live_threads": len(_live_threads),
            "board_sig_cache": len(_LAST_BOARD_SIG),
            "auth_required": bool(config.CONFIG.api_token),
            "pending_batches": pending_retries,
            "pending_by_meeting": pending_by_meeting,
            "llm": llm_metrics()}


@app.post("/api/utterances", dependencies=[Depends(require_token)])
async def receive_utterance(event: IncomingUtterance):
    _require_meeting_open_for_automatic_input(event.meeting_id)
    return await utterance_ingestor.ingest(event)


@app.get("/api/utterances/{utterance_id}/status")
async def utterance_status(
    utterance_id: str,
    meeting_id: Annotated[str, Query(pattern=MEETING_ID_PATTERN)],
):
    record = store_b.get(metadata_id(meeting_id, utterance_id))
    if not record or record.get("kind") != "utt":
        raise HTTPException(status_code=404, detail="utterance not found")
    processing = record.get("processing_state")
    state = {"done": "committed", "processing": "processing", "failed": "failed"}.get(processing, "accepted")
    return {
        "utterance_id": utterance_id,
        "meta_id": record.get("meta_id"),
        "state": state,
        "attempts": int(record.get("attempts") or 0),
        "last_error": record.get("last_error"),
        "board_version": store_a.version(meeting_id) if state == "committed" else None,
        "board_effect": utterance_ingestor.board_effect(meeting_id, record["meta_id"])
        if state == "committed" else None,
        "updated_at_ms": record.get("updated_at_ms"),
    }


@app.get("/api/board")
async def get_board(meeting_id: Annotated[str, Query(pattern=MEETING_ID_PATTERN)]):
    if not store_a.exists(meeting_id):
        raise HTTPException(status_code=404, detail="meeting not found")
    summary = next((item for item in store_a.list_meetings()
                    if item["meeting_id"] == meeting_id), {})
    details = store_a.meeting_details(meeting_id) or {}
    return {"graph_id": meeting_id, "schema": summary.get("schema", "amo.board/v1"),
            "version": store_a.version(meeting_id), "cells": store_a.load(meeting_id),
            "title": details.get("title"), "status": details.get("status", "draft"),
            "agenda": details.get("agenda", [])}


class CreateMeetingRequest(BaseModel):
    title: str = Field(min_length=1, max_length=60)
    agenda_text: str = Field(default="", max_length=100000)


class MeetingStatusRequest(BaseModel):
    status: str = Field(pattern="^(live|ended)$")


class RenameMeetingRequest(BaseModel):
    title: str = Field(min_length=1, max_length=60)


def _valid_path_meeting_id(graph_id: str) -> str:
    if not is_valid_meeting_id(graph_id):
        raise HTTPException(status_code=400, detail="invalid meeting_id")
    return graph_id


def _require_meeting_open_for_automatic_input(meeting_id: str) -> None:
    details = store_a.meeting_details(meeting_id)
    if details and details.get("status") == "ended":
        raise HTTPException(status_code=409,
                            detail="meeting has ended; reopen it before sending automatic input")


@app.get("/api/meetings")
async def list_meetings():
    return {"default_meeting_id": config.CONFIG.default_meeting_id,
            "meetings": store_a.list_meetings()}


@app.get("/api/experts")
async def list_experts():
    """会议专家技能包（prompts.yaml experts）：供前端「会议专家」下拉框选项。"""
    return {"experts": [{"id": key, "label": (val or {}).get("label", key)}
                        for key, val in prompts.EXPERTS.items()]}


def _resolve_expert(expert) -> Optional[str]:
    """校验前端传来的专家 id；未知 id 直接拒绝（防止静默回退造成误导）。"""
    if not expert:
        return None
    if str(expert) not in prompts.EXPERTS:
        raise HTTPException(status_code=422, detail=f"unknown expert: {expert}")
    return str(expert)


@app.post("/api/meetings", dependencies=[Depends(require_token)])
async def create_meeting(req: CreateMeetingRequest):
    title = req.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="title cannot be empty")
    meeting_id = f"mtg_{uuid.uuid4().hex[:16]}"
    agenda = parse_agenda(req.agenda_text)
    return {"ok": True, **store_a.create_meeting(meeting_id, title, agenda, req.agenda_text)}


@app.get("/api/meetings/{graph_id}/close-preview")
async def meeting_close_preview(graph_id: str):
    graph_id = _valid_path_meeting_id(graph_id)
    details = store_a.meeting_details(graph_id)
    if details is None:
        raise HTTPException(status_code=404, detail="meeting not found")
    return {"ok": True, "graph_id": graph_id,
            "review": review_before_close(store_a.load(graph_id))}


@app.post("/api/meetings/{graph_id}/status", dependencies=[Depends(require_token)])
async def update_meeting_status(graph_id: str, req: MeetingStatusRequest):
    graph_id = _valid_path_meeting_id(graph_id)
    try:
        result = store_a.set_meeting_status(graph_id, req.status)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="meeting not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _broadcast_board(graph_id)
    return {"ok": True, **result,
            "review": review_before_close(store_a.load(graph_id)) if req.status == "ended" else None}


@app.get("/api/meetings/{graph_id}/minutes")
async def meeting_minutes(graph_id: str, format: str = Query(default="markdown", pattern="^(markdown|html)$")):
    graph_id = _valid_path_meeting_id(graph_id)
    details = store_a.meeting_details(graph_id)
    if details is None:
        raise HTTPException(status_code=404, detail="meeting not found")
    content = render_minutes(details["title"] or graph_id, details["agenda"], details["status"],
                             store_a.load(graph_id), store_b.list_utterances(graph_id),
                             format=format, history=store_a.history(graph_id))
    filename = f"{graph_id}-minutes.{ 'html' if format == 'html' else 'md' }"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if format == "html":
        return HTMLResponse(content, headers=headers)
    return PlainTextResponse(content, media_type="text/markdown; charset=utf-8", headers=headers)


@app.get("/api/meetings/{graph_id}/snapshot-file")
async def meeting_snapshot_file(graph_id: str):
    """Download a local archive snapshot; unlike a URL, this file travels with the user."""
    graph_id = _valid_path_meeting_id(graph_id)
    details = store_a.meeting_details(graph_id)
    if details is None:
        raise HTTPException(status_code=404, detail="meeting not found")
    document = {
        "format": "amo.meeting-snapshot/v1", "readonly": True,
        "exported_at": store_a._now_iso(),
        "meeting": details,
        "schema": next((row.get("schema") for row in store_a.list_meetings()
                        if row.get("meeting_id") == graph_id), "amo.board/v1"),
        "cells": store_a.load(graph_id),
        "utterances": store_b.list_utterances(graph_id),
        "history": store_a.history(graph_id),
    }
    return JSONResponse(document, headers={
        "Content-Disposition": f'attachment; filename="{graph_id}-snapshot.json"',
    })


@app.get("/api/board/{graph_id}")
async def get_board_by_id(graph_id: str):
    graph_id = _valid_path_meeting_id(graph_id)
    if not store_a.exists(graph_id):
        raise HTTPException(status_code=404, detail="meeting not found")
    cells = store_a.load(graph_id)
    summary = next((item for item in store_a.list_meetings()
                    if item["meeting_id"] == graph_id), None) or {}
    details = store_a.meeting_details(graph_id) or {}
    return {"graph_id": graph_id, "schema": summary.get("schema", "amo.board/v1"),
            "version": summary.get("version", 0),
            "updated_at": summary.get("updated_at"), "title": summary.get("title"),
            "status": details.get("status", "draft"), "agenda": details.get("agenda", []),
            "cells": cells}


@app.get("/api/board/{graph_id}/outline")
async def get_board_outline(graph_id: str):
    graph_id = _valid_path_meeting_id(graph_id)
    return {"graph_id": graph_id, "outline": prompts.serialize_for_llm(store_a.load(graph_id))}


@app.get("/api/board/{graph_id}/migration-preview")
def get_board_migration_preview(graph_id: str):
    """Read-only preview for a v1 board; applying migration remains gated until v2 readers ship."""
    graph_id = _valid_path_meeting_id(graph_id)
    try:
        preview = store_a.migration_preview(graph_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if preview is None:
        raise HTTPException(status_code=404, detail="meeting not found")
    return {"ok": True, "graph_id": graph_id, "preview": preview}


class MigrationRequest(BaseModel):
    expected_version: int = Field(ge=0)


@app.post("/api/board/{graph_id}/migration-accept", dependencies=[Depends(require_token)])
async def accept_board_migration(graph_id: str, req: MigrationRequest):
    graph_id = _valid_path_meeting_id(graph_id)
    try:
        result = store_a.accept_v1_migration(graph_id, expected_version=req.expected_version)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RuntimeError, FileExistsError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _broadcast_board(graph_id)
    return {"ok": True, **result}


@app.post("/api/board/{graph_id}/migration-rollback", dependencies=[Depends(require_token)])
async def rollback_board_migration(graph_id: str, req: MigrationRequest):
    graph_id = _valid_path_meeting_id(graph_id)
    try:
        result = store_a.rollback_v1_migration(graph_id, expected_version=req.expected_version)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RuntimeError, FileExistsError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _broadcast_board(graph_id)
    return {"ok": True, **result}


class UpdateRequest(BaseModel):
    operations: list = Field(default_factory=list)
    thought: str = ""


@app.post("/api/board/{graph_id}/update", dependencies=[Depends(require_token)])
async def post_board_update(graph_id: str, req: UpdateRequest):
    graph_id = _valid_path_meeting_id(graph_id)
    if not store_a.exists(graph_id):
        raise HTTPException(status_code=404, detail="meeting not found")
    fields = GraphOp.__dataclass_fields__
    try:
        operations = [GraphOp(**{k: v for k, v in item.items() if k in fields})
                      for item in req.operations if isinstance(item, dict)]
        result = graph_tools.update_graph(graph_id, GraphUpdateOp(
            operations=operations, thought=req.thought))
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _broadcast_board(graph_id)
    return {"ok": result.ok, "graph_id": graph_id, "version": store_a.version(graph_id),
            "cells": store_a.load(graph_id), "skipped": result.skipped,
            "repair_receipt": (result.data or {}).get("repair_receipt")}


class SaveBoardRequest(BaseModel):
    edits: dict = Field(default_factory=dict)


@app.post("/api/board/{graph_id}/save", dependencies=[Depends(require_token)])
async def save_board(graph_id: str, req: Optional[SaveBoardRequest] = None):
    graph_id = _valid_path_meeting_id(graph_id)
    record = store_a.save_snapshot(graph_id, req.edits if req else None)
    if record is None:
        raise HTTPException(status_code=404, detail="meeting not found")
    await _broadcast_board(graph_id)
    return {"ok": True, "graph_id": graph_id, "version": record["version"],
            "updated_at": record["created_at"]}


@app.post("/api/meetings/{graph_id}/rename", dependencies=[Depends(require_token)])
async def rename_meeting(graph_id: str, req: RenameMeetingRequest):
    graph_id = _valid_path_meeting_id(graph_id)
    title = req.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="title cannot be empty")
    result = store_a.rename_meeting(graph_id, title)
    if result is None:
        raise HTTPException(status_code=404, detail="meeting not found")
    await _broadcast_board(graph_id)
    return {"ok": True, **result}


@app.get("/api/meetings/{graph_id}/utterances")
async def meeting_utterances(graph_id: str):
    """逐句对话记录（Store B 原始发言，按时间序），供历史页展示。"""
    graph_id = _valid_path_meeting_id(graph_id)
    if not store_a.exists(graph_id):
        raise HTTPException(status_code=404, detail="meeting not found")
    return {"graph_id": graph_id, "utterances": store_b.list_utterances(graph_id)}


@app.get("/api/board/{graph_id}/history")
async def board_history(graph_id: str, cell_id: Optional[str] = None):
    graph_id = _valid_path_meeting_id(graph_id)
    if not store_a.exists(graph_id):
        raise HTTPException(status_code=404, detail="meeting not found")
    records = store_a.history(graph_id)
    if cell_id:
        records = [record for record in records
                   if any(cell.get("id") == cell_id for cell in record.get("cells", []))]
    return {"graph_id": graph_id, "records": records}


@app.post("/api/board/{graph_id}/snapshot", dependencies=[Depends(require_token)])
async def board_snapshot(graph_id: str, req: Optional[SaveBoardRequest] = None):
    graph_id = _valid_path_meeting_id(graph_id)
    result = store_a.create_share_snapshot(graph_id, req.edits if req else None)
    if result is None:
        raise HTTPException(status_code=404, detail="meeting not found")
    await _broadcast_board(graph_id)
    return {"ok": True, **result}


@app.get("/api/view/{token}")
async def view_snapshot(token: str):
    snapshot = store_a.load_share_snapshot(token)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="snapshot not found")
    return {"ok": True, "readonly": True, "doc": snapshot}


@app.get("/api/metadata")
async def get_metadata(ids: str = ""):
    meta_ids = [i for i in ids.split(",") if i]
    return {"records": store_b.get_many(meta_ids)}


@app.get("/api/cursor/config")
async def cursor_config_get():
    return {"throttle_ms": config.CONFIG.throttle_ms,
            "hover_settle_ms": config.CONFIG.hover_settle_ms,
            "drag_px_threshold": config.CONFIG.drag_px_threshold,
            "cursor_independent_trigger": config.CONFIG.cursor_independent_trigger,
            "cursor_fusion_window_ms": config.CONFIG.cursor_fusion_window_ms}


@app.post("/api/cursor/config", dependencies=[Depends(require_token)])
async def cursor_config_post(cfg: dict):
    """接收前端上报/调优。仅接受白名单字段且做类型/范围校验，防止任意覆写全局配置。"""
    applied = {}
    for k, v in cfg.items():
        spec = _MUTABLE_CURSOR_CONFIG.get(k)
        if spec is None:
            continue
        ok, coerced = _validated(v, spec)
        if ok:
            applied[k] = coerced
    config.apply_overrides(**applied)          # 不可变替换全局单例，非就地 setattr
    rejected = sorted(set(cfg) - set(applied))
    if rejected:
        log.warning("[cursor/config] rejected keys: %s", rejected)
    return {"ok": True, "applied": sorted(applied)}


@app.get("/api/mascot/config")
async def mascot_config():
    return {"idle_return_ms": 1500,
            "states": ["idle", "listening", "filtering", "loading_board", "assembling",
                       "analyzing", "syncing", "parsing", "updating", "success", "error"]}


# 各 meeting 上次广播的看板签名：内容未变则不重复下发全图（cursor 空转批次零广播）。
# OrderedDict + 上限淘汰：避免长跑/多会议下无界累积。
_MAX_BOARD_SIG = 256
_LAST_BOARD_SIG: "OrderedDict[str, tuple]" = OrderedDict()
# checkpointer 热线程上限：超出按 LRU 淘汰（看板已落 Store A，可冷启动重载）
_MAX_LIVE_THREADS = 64
_live_threads: "OrderedDict[str, None]" = OrderedDict()


def _touch_thread(meeting_id: str) -> None:
    """记录活跃会议并淘汰最旧的 checkpointer 线程，避免内存单调上涨。"""
    _live_threads.pop(meeting_id, None)
    _live_threads[meeting_id] = None
    while len(_live_threads) > _MAX_LIVE_THREADS:
        old, _ = _live_threads.popitem(last=False)
        agent.evict_thread(old)


# 每会议编排串行锁（P0①）：ASR 合批 flush / CLI 推送 / 补偿重放可能并发驱动
# 同一会议，串行化后避免 checkpointer 状态与看板落库互相踩踏。
_MAX_DRIVE_LOCKS = 128
_drive_locks: "OrderedDict[str, asyncio.Lock]" = OrderedDict()


def _drive_lock(meeting_id: str) -> asyncio.Lock:
    lock = _drive_locks.get(meeting_id)
    if lock is None:
        lock = _drive_locks[meeting_id] = asyncio.Lock()
        _drive_locks.move_to_end(meeting_id)
        while len(_drive_locks) > _MAX_DRIVE_LOCKS:
            _drive_locks.popitem(last=False)
    return lock


def _board_sig(cells: list) -> tuple:
    return tuple(json.dumps(cell, sort_keys=True, ensure_ascii=False) for cell in cells)


async def _broadcast_board(meeting_id: str, receipt: Optional[dict] = None) -> None:
    cells = store_a.load(meeting_id)
    sig = _board_sig(cells)
    if _LAST_BOARD_SIG.get(meeting_id) == sig:
        return
    _LAST_BOARD_SIG[meeting_id] = sig
    _LAST_BOARD_SIG.move_to_end(meeting_id)
    while len(_LAST_BOARD_SIG) > _MAX_BOARD_SIG:
        _LAST_BOARD_SIG.popitem(last=False)
    event = {"type": "board.update", "graph_id": meeting_id,
             "version": store_a.version(meeting_id), "cells": cells}
    if receipt:
        event["repair_receipt"] = receipt
        event["change_set"] = receipt.get("change_set")
    await bus.publish(event)


def _buffer_cursor(meeting_id: str, events: list) -> None:
    now_ms = int(time.time() * 1000)
    ttl = config.CONFIG.cursor_fusion_window_ms
    fresh = [e for e in _cursor_buffers.get(meeting_id, [])
             if now_ms - e.received_at_ms <= ttl]
    fresh.extend(events)
    _cursor_buffers[meeting_id] = fresh[-100:]


def _drain_cursor(meeting_id: str) -> list:
    events = _cursor_buffers.pop(meeting_id, [])
    now_ms = int(time.time() * 1000)
    ttl = config.CONFIG.cursor_fusion_window_ms
    return [e for e in events if now_ms - e.received_at_ms <= ttl]


async def _drive(meeting_id: str, raw_utterances=None, raw_cursor=None,
                 meeting_title=None, expert=None, _retry: bool = False):
    """把一批事件喂给编排器，并广播结果（mascot_state + board.update）。

    每会议串行（P0①）：并发驱动同一会议会互相踩 checkpointer 状态。
    补偿（P0①）：LLM/落库失败的批次记 pending_batch（Store B），供后台轮询
    与 POST /api/meetings/{id}/retry_pending 重驱动；_retry=True 为重放路径，
    不再重复记录（由 _retry_pending_batches 负责更新 attempts/清除标记）。
    """
    async with _drive_lock(meeting_id):
        cursor = list(raw_cursor or [])
        if raw_utterances:
            cursor.extend(_drain_cursor(meeting_id))
        result = await agent.run(meeting_id, raw_utterances or [], cursor, meeting_title,
                                 expert=expert)
        _touch_thread(meeting_id)
        ms = result.get("mascot_state", "idle") if isinstance(result, dict) \
            else getattr(result, "mascot_state", "idle")
        await bus.publish({"type": "mascot_state", "meeting_id": meeting_id,
                           "state": ms, "label": ms})
        receipt = result.get("repair_receipt") if isinstance(result, dict) \
            else getattr(result, "repair_receipt", None)
        await _broadcast_board(meeting_id, receipt)
        error = result.get("error") if isinstance(result, dict) else None
        if raw_utterances and error and not _retry:
            _record_pending(meeting_id, raw_utterances, error)
    return result


# ---------------------------------------------------------------------------
# 失败批次补偿（P0①）：pending_batch 记录 + 后台/手动重驱动
# ---------------------------------------------------------------------------
def _pending_batch_id(meeting_id: str, utts: list) -> str:
    """批次确定性 id：同一批语句重记录覆盖同一条 pending（幂等）。"""
    ids = ",".join(sorted(u.utterance_id for u in utts))
    return f"pend_{stable_hash(f'{meeting_id}:{ids}', 10 ** 10)}"


def _record_pending(meeting_id: str, utts: list, error) -> None:
    pid = _pending_batch_id(meeting_id, utts)
    prev = store_b.get(pid) or {}
    store_b.put(pid, {
        "meta_id": pid, "kind": "pending_batch", "meeting_id": meeting_id,
        "utterance_ids": [u.utterance_id for u in utts],
        "attempts": int(prev.get("attempts") or 0),
        "last_error": safe_error_code(error),
        "updated_at_ms": int(time.time() * 1000),
    })
    log.warning("[pending] batch of %d utt recorded for %s (%s)",
                len(utts), meeting_id, safe_error_code(error))


_PENDING_RETRY_LOCK = asyncio.Lock()


async def _retry_pending_batches(meeting_id: Optional[str] = None,
                                 *, force_exhausted: bool = False) -> dict:
    """Serialize recovery runs so the poller and manual action cannot replay a batch twice."""
    async with _PENDING_RETRY_LOCK:
        return await _retry_pending_batches_locked(meeting_id, force_exhausted=force_exhausted)


async def _retry_pending_batches_locked(meeting_id: Optional[str] = None,
                                         *, force_exhausted: bool = False) -> dict:
    """重驱动失败批次：重建 NormUtterance → 编排 → 成功清除标记 / 失败累计 attempts。"""
    cfg = config.CONFIG
    recovered = still_failing = skipped = 0
    for b in store_b.list_by_kind("pending_batch", meeting_id):
        attempts = int(b.get("attempts") or 0)
        if attempts >= cfg.pending_retry_max_attempts and not force_exhausted:
            skipped += 1
            continue
        m = b.get("meeting_id")
        recs = [r for r in (store_b.get(i) for i in b.get("utterance_ids") or []) if r]
        if not recs:
            store_b.delete(b["meta_id"])      # 原始语句已不存在：无法重放，清除标记
            continue
        active = []
        for rec in recs:
            processing = {**rec, "processing_state": "processing",
                          "attempts": int(rec.get("attempts") or 0) + 1,
                          "last_error": None, "updated_at_ms": int(time.time() * 1000)}
            store_b.put(rec["meta_id"], processing)
            active.append(processing)
            await bus.publish({"type": "utterance.status", "meeting_id": m,
                               "meta_id": rec["meta_id"],
                               "utterance_id": rec.get("source_utterance_id"),
                               "state": "processing", "attempts": processing["attempts"],
                               "last_error": None})
        store_b.flush(force=True)
        utts = [from_stored_record(m, r) for r in recs]
        result = await _drive(m, raw_utterances=utts,
                              meeting_title=store_a.title(m), _retry=True)
        error = result.get("error") if isinstance(result, dict) else None
        for rec in active:
            utterance = {**rec, "processing_state": "failed" if error else "done",
                         "last_error": safe_error_code(error) if error else None,
                         "updated_at_ms": int(time.time() * 1000)}
            store_b.put(rec["meta_id"], utterance)
            await bus.publish({"type": "utterance.status", "meeting_id": m,
                               "meta_id": rec["meta_id"],
                               "utterance_id": rec.get("source_utterance_id"),
                               "state": "failed" if error else "committed",
                               "attempts": int(utterance.get("attempts") or 0),
                               "last_error": utterance["last_error"]})
        store_b.flush(force=True)
        if error:
            store_b.put(b["meta_id"], {**b, "attempts": attempts + 1,
                                       "last_error": safe_error_code(error),
                                       "updated_at_ms": int(time.time() * 1000)})
            still_failing += 1
        else:
            store_b.delete(b["meta_id"])
            recovered += 1
    return {"recovered": recovered, "still_failing": still_failing,
            "skipped_max_attempts": skipped}


utterance_ingestor = UtteranceIngestor(
    store_a, store_b, _drive, debounce_ms=config.CONFIG.realtime_debounce_ms,
    status_changed=bus.publish)


def _persist_utterance(meeting_id: str, u) -> bool:
    """原始语句入库（Store B）；幂等：以 utterance_id 为准，已存在返回 False。

    是 asr_push / cli_push / cli_push_batch 三路共用的唯一去重入口：
    ASR 流式重连、网络超时重发都会命中同一 utterance_id，从而不重复入库/编排。
    """
    if store_b.get(u.utterance_id):
        return False
    store_b.put(u.utterance_id, {"meta_id": u.utterance_id, "kind": "utt", "text": u.text,
                 "meeting_id": meeting_id,
                 "speaker_ref": u.speaker.speaker_ref,
                 "display_name": u.speaker.display_name,
                 "start_offset_ms": u.start_offset_ms, "end_offset_ms": u.end_offset_ms,
                 "source": u.source})
    return True


async def _store_utterance(meeting_id: str, u, meeting_title: Optional[str] = None,
                           expert: Optional[str] = None) -> bool:
    """入库 + 自适应合批驱动编排。

    幂等：以 utterance_id 为准，已入库的重复语句（超时重发）跳过，返回 False。
    合批：推流快于 LLM 消化时，同一会议等锁积压的句子按块（≤cli_max_block）
    合并成一次 agent.run——推得慢退化为逐句，推得快自动成块，响应语义不变
    （POST 仍等待该句所在批次编排完成才返回）。
    """
    if not _persist_utterance(meeting_id, u):
        return False
    item = _QueuedUtt(u=u, meeting_title=meeting_title or store_a.title(meeting_id),
                      expert=expert, done=asyncio.get_running_loop().create_future())
    _enqueue_utt(meeting_id, item)
    try:
        await item.done                                   # 等自己所在批次编排完成
    except asyncio.CancelledError:
        raise
    except Exception:                                     # 编排失败：已补记 pending_batch，
        return True                                       # 响应仍按「已入库」确认（非重复）
    return True


class _QueuedUtt:
    """已入库、等待与同会议积压句合并驱动的语句。"""
    __slots__ = ("u", "meeting_title", "expert", "done")

    def __init__(self, u, meeting_title, expert, done: asyncio.Future):
        self.u = u
        self.meeting_title = meeting_title
        self.expert = expert
        self.done = done


_utt_queues: "OrderedDict[str, list[_QueuedUtt]]" = OrderedDict()   # meeting_id -> FIFO
_utt_workers: dict[str, asyncio.Task] = {}
_MAX_UTT_QUEUES = 128


def _enqueue_utt(meeting_id: str, item: "_QueuedUtt") -> None:
    q = _utt_queues.get(meeting_id)
    if q is None:
        _utt_queues[meeting_id] = q = []
        while len(_utt_queues) > _MAX_UTT_QUEUES:
            _utt_queues.popitem(last=False)
    q.append(item)
    w = _utt_workers.get(meeting_id)
    if w is None or w.done():
        _utt_workers[meeting_id] = asyncio.create_task(_utt_worker(meeting_id))


async def _utt_worker(meeting_id: str) -> None:
    """每会议一个驱动循环：拿锁后整块取走当前积压（≤上限），一次 _drive 消化。

    块大小随推流节奏自适应：LLM 处理期间到达的句子在下轮被并批；
    批内 title/expert 取末句非空值（同一推流会话内语义一致）。
    """
    try:
        while True:
            await asyncio.sleep(config.CONFIG.cli_coalesce_ms / 1000)  # 起跑窗：等紧邻句并块
            q = _utt_queues.get(meeting_id)
            if not q:
                return
            block = q[:config.CONFIG.cli_max_block]           # 头部整块摘出（≤上限）
            del q[:len(block)]
            utts = [it.u for it in block]
            title = next((it.meeting_title for it in reversed(block) if it.meeting_title), None)
            expert = next((it.expert for it in reversed(block) if it.expert), None)
            try:
                result = await _drive(meeting_id, raw_utterances=utts,
                                      meeting_title=title, expert=expert)
                for it in block:
                    if not it.done.done():
                        it.done.set_result(result)
            except Exception as e:                            # agent.run 抛错：补补偿标记 + 告知等待方
                _record_pending(meeting_id, utts, e)
                for it in block:
                    if not it.done.done():
                        it.done.set_exception(e)
    finally:
        _utt_workers.pop(meeting_id, None)


async def _handle_uplink(msg: dict) -> None:
    """处理 WS 上行事件（Design §3.5：cursor.event / asr.event）。

    cursor.event：信封 {event, version, meeting_id, events:[原始X6事件]} →
    逐条 from_x6_event 归一化 → 驱动编排（与 REST /api/cursor/push 对等）。
    ASR 信源当前走 REST /api/asr/push；asr.event 信封待接真实 ASR 信源时在此归一化。
    """
    if msg.get("event") != "cursor.event":
        return
    meeting_id = msg.get("meeting_id") or config.CONFIG.default_meeting_id
    if not is_valid_meeting_id(meeting_id):
        return
    _buffer_cursor(meeting_id,
                   [from_x6_event(meeting_id, raw) for raw in msg.get("events") or []])


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    q = bus.subscribe()

    async def downstream():
        while True:                          # 下行：总线事件推给该连接
            event = await q.get()
            await ws.send_json(event)

    async def upstream():
        while True:                          # 上行：接收前端 cursor.event 等
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                continue                     # 忽略非法 JSON 帧，不因此断开连接
            if isinstance(msg, dict):
                await _handle_uplink(msg)

    tasks = [asyncio.create_task(downstream()), asyncio.create_task(upstream())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    finally:
        for t in tasks:                      # 断开/异常时停掉对端任务并退订（防死队列泄漏）
            t.cancel()
        bus.unsubscribe(q)
    for t in done:
        if not t.cancelled() and t.exception() \
                and not isinstance(t.exception(), WebSocketDisconnect):
            log.warning("[WS] connection task error (%s)", safe_error_code(t.exception()))


async def _ws_downstream(ws: WebSocket, predicate) -> None:
    queue = bus.subscribe()
    try:
        while True:
            event = await queue.get()
            if predicate(event):
                await ws.send_json(event)
    finally:
        bus.unsubscribe(queue)


@app.websocket("/ws/board/{meeting_id}")
async def ws_board(ws: WebSocket, meeting_id: str):
    if not is_valid_meeting_id(meeting_id):
        await ws.close(code=1008)
        return
    await ws.accept()
    try:
        await _ws_downstream(ws, lambda event: event.get("type") in
                             {"board.update", "board.rollback"} and
                             event.get("graph_id") == meeting_id)
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/mascot/{meeting_id}")
async def ws_mascot(ws: WebSocket, meeting_id: str):
    if not is_valid_meeting_id(meeting_id):
        await ws.close(code=1008)
        return
    await ws.accept()
    try:
        await _ws_downstream(ws, lambda event: event.get("type") == "mascot_state"
                             and event.get("meeting_id") == meeting_id)
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/cursor")
async def ws_cursor(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            msg = await ws.receive_json()
            if isinstance(msg, dict):
                await _handle_uplink(msg)
    except WebSocketDisconnect:
        pass


@app.post("/api/asr/push", dependencies=[Depends(require_token)])
async def asr_push(meeting_id: Annotated[str, Query(pattern=MEETING_ID_PATTERN)], payload: dict, meeting_title: str = None):
    """接收腾讯会议 asr-push 单句 → 归一化 → 入库（幂等）→ 驱动编排。

    幂等：按 utterance_id（腾讯 sid 天然确定性）去重，重发同 sid 直接确认返回。
    实时合批（P1②）已下沉到生产入口 POST /api/utterances（UtteranceIngestor）；
    本端点供 webhook 单句直驱，不走合批。
    """
    _require_meeting_open_for_automatic_input(meeting_id)
    u = from_tencent_asr_push(meeting_id, payload)
    if not _persist_utterance(meeting_id, u):
        return {"ok": True, "utterance_id": u.utterance_id, "deduplicated": True}
    expert = _resolve_expert(payload.get("expert") if isinstance(payload, dict) else None)
    await _drive(meeting_id, raw_utterances=[u],
                 meeting_title=meeting_title or store_a.title(meeting_id), expert=expert)
    return {"ok": True, "utterance_id": u.utterance_id}


@app.post("/api/cursor/push", dependencies=[Depends(require_token)])
async def cursor_push(meeting_id: Annotated[str, Query(pattern=MEETING_ID_PATTERN)], raw: dict):
    """接收光标事件并缓冲，等待下一批语音发言合并处理。"""
    e = from_x6_event(meeting_id, raw)
    _buffer_cursor(meeting_id, [e])
    return {"ok": True}


class CliPushRequest(BaseModel):
    """CLI 调试口请求体：一行键入文本 → 归一化 → 驱动编排。"""
    meeting_id: str = Field(pattern=MEETING_ID_PATTERN)
    text: str
    speaker_ref: str = "cli:user"
    display_name: str = "CLI调试"
    meeting_title: Optional[str] = None
    expert: Optional[str] = None       # 会议专家技能 id（prompts.yaml experts），随批生效
    utterance_id: Optional[str] = None  # 客户端确定性 id：重试重发时服务端按此幂等去重


@app.post("/api/cli/push", dependencies=[Depends(require_token)])
async def cli_push(req: CliPushRequest):
    """CLI 调试口：一行键入文本 → 归一化 → 驱动编排（与腾讯会议 / FunASR 对等流入）。

    调试流：cli_debug.py → POST 本端点 → 复用 from_cli_text + _store_utterance。
    支持 speaker_ref 区分多人（如 cli:user_zhang）；meeting_title 仅在首次建会时用于根节点标题；
    expert 为当前「会议专家」下拉框所选技能，影响本批要点解析与节点绘制的提示词。
    幂等：带 utterance_id 的重复请求（如客户端超时重发）只入库/编排一次，直接确认返回
    （去重由 _store_utterance 统一兜底）。
    """
    _require_meeting_open_for_automatic_input(req.meeting_id)
    u = from_cli_text(req.meeting_id, req.text, speaker_ref=req.speaker_ref,
                      display_name=req.display_name, utterance_id=req.utterance_id)
    stored = await _store_utterance(req.meeting_id, u, req.meeting_title,
                                    expert=_resolve_expert(req.expert))
    return {"ok": True, "utterance_id": u.utterance_id, "deduplicated": not stored}


class CliPushBatchItem(BaseModel):
    """批量口单行：与 CliPushRequest 对等的行级字段。"""
    text: str
    speaker_ref: str = "cli:user"
    display_name: str = "CLI调试"
    utterance_id: Optional[str] = None   # 确定性 id：重发/续灌幂等去重


class CliPushBatchRequest(BaseModel):
    """CLI 批量调试口请求体：多行文本 → 单批次驱动编排（历史回灌用）。"""
    meeting_id: str = Field(pattern=MEETING_ID_PATTERN)
    items: list[CliPushBatchItem] = Field(min_length=1, max_length=50)
    meeting_title: Optional[str] = None
    expert: Optional[str] = None


@app.post("/api/cli/push_batch", dependencies=[Depends(require_token)])
async def cli_push_batch(req: CliPushBatchRequest):
    """CLI 批量调试口：N 行文本一次 analyze + 一次 sync，摊薄 LLM 成本。

    与逐句口对等流入编排器（raw_utterances 列表），仅合并驱动时机；
    幂等：按行级 utterance_id 去重，已入库的行直接跳过，不重复灌入。
    """
    _require_meeting_open_for_automatic_input(req.meeting_id)
    utterances = []
    skipped = 0
    for it in req.items:
        u = from_cli_text(
            req.meeting_id, it.text, speaker_ref=it.speaker_ref,
            display_name=it.display_name, utterance_id=it.utterance_id)
        if not _persist_utterance(req.meeting_id, u):   # 行级幂等去重（统一入口）
            skipped += 1
            continue
        utterances.append(u)
    if not utterances:
        return {"ok": True, "accepted": 0, "skipped": skipped, "deduplicated": True}
    await _drive(req.meeting_id, raw_utterances=utterances,
                 meeting_title=req.meeting_title or store_a.title(req.meeting_id),
                 expert=_resolve_expert(req.expert))
    return {"ok": True, "accepted": len(utterances), "skipped": skipped,
            "utterance_ids": [u.utterance_id for u in utterances]}


@app.post("/api/meetings/{meeting_id}/retry_pending", dependencies=[Depends(require_token)])
async def retry_pending(meeting_id: str):
    """手动重驱动该会议的失败批次（P0①；后台每 pending_retry_interval_s 也会自动轮询）。

    手动调用会重试已达到自动上限的批次；后台轮询仍遵守 attempts 上限，避免失败时持续重放。
    成功的批次清除 pending 标记并广播看板更新；失败批次保留原文和错误状态供再次处理。
    """
    meeting_id = _valid_path_meeting_id(meeting_id)
    res = await _retry_pending_batches(meeting_id, force_exhausted=True)
    return {"ok": True, "meeting_id": meeting_id, **res}


class ToolInvokeRequest(BaseModel):
    tool: str
    args: dict = Field(default_factory=dict)


@app.post("/api/tools/{meeting_id}/invoke", dependencies=[Depends(require_token)])
async def tools_invoke(meeting_id: str, req: ToolInvokeRequest):
    """沿用 master 的统一工具入口，图写入后向当前会议广播更新。"""
    meeting_id = _valid_path_meeting_id(meeting_id)
    if not store_a.exists(meeting_id):
        raise HTTPException(status_code=404, detail="meeting not found")
    a = req.args
    try:
        if req.tool == "get_board":
            result = graph_tools.get_board(meeting_id)
        elif req.tool == "search_nodes":
            result = graph_tools.search_nodes(meeting_id, str(a.get("query", "")))
        elif req.tool == "update_graph":
            fields = GraphOp.__dataclass_fields__
            ops = [GraphOp(**{k: v for k, v in item.items() if k in fields})
                   for item in a.get("operations", []) if isinstance(item, dict)]
            result = graph_tools.update_graph(meeting_id, GraphUpdateOp(
                operations=ops, thought=str(a.get("thought", ""))))
        elif req.tool == "create_node":
            result = graph_tools.create_node(meeting_id, str(a.get("node_id", "")),
                str(a.get("node_type", "point")), str(a.get("label", "")), a.get("meta_ids"))
        elif req.tool == "lock_node":
            result = graph_tools.lock_node(meeting_id, str(a.get("node_id", "")),
                str(a.get("locked_by", "human")), bool(a.get("locked", True)))
        elif req.tool == "set_importance":
            result = graph_tools.set_importance(meeting_id, str(a.get("node_id", "")),
                str(a.get("level", "normal")))
        elif req.tool == "fetch_metadata":
            result = metadata_tools.fetch_metadata(list(a.get("meta_ids") or []))
        elif req.tool == "get_node":
            result = metadata_tools.get_node(meeting_id, str(a.get("node_id", "")))
        else:
            raise HTTPException(status_code=400, detail=f"unknown tool: {req.tool}")
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if req.tool in {"update_graph", "create_node", "lock_node", "set_importance"} and result.ok:
        await _broadcast_board(meeting_id)
    return {"ok": result.ok, "tool": req.tool,
            "data": result.data, "skipped": result.skipped}


class UserOpRequest(BaseModel):
    graph_id: str = ""
    op: str
    payload: dict = Field(default_factory=dict)
    actor: str = "human"
    expected_version: int = Field(ge=0)


class NodePatchRequest(BaseModel):
    graph_id: str = ""
    expected_version: int
    fields: dict
    actor: str = "human"
    reason: str = ""


class NodeMergeRequest(BaseModel):
    duplicate_id: str
    survivor_id: str
    expected_version: int
    actor: str = "human"
    reason: str = ""


class UndoOperationRequest(BaseModel):
    operation_version: int = Field(ge=1)
    expected_version: int = Field(ge=0)
    actor: str = "human"


@app.post("/api/board/{graph_id}/undo", dependencies=[Depends(require_token)])
async def undo_board_operation(graph_id: str, req: UndoOperationRequest):
    graph_id = _valid_path_meeting_id(graph_id)
    try:
        cells = store_a.undo_operation(graph_id, req.operation_version,
            expected_version=req.expected_version, actor=req.actor)
    except VersionConflict as exc:
        return JSONResponse(status_code=409, content={"detail": "board version changed",
            "current_version": exc.current_version, "cells": exc.cells})
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _broadcast_board(graph_id)
    return {"ok": True, "version": store_a.version(graph_id), "cells": cells}


@app.post("/api/board/{graph_id}/merge-preview", dependencies=[Depends(require_token)])
async def preview_node_merge(graph_id: str, req: NodeMergeRequest):
    graph_id = _valid_path_meeting_id(graph_id)
    try:
        preview = store_a.preview_node_merge(graph_id, req.duplicate_id, req.survivor_id)
        if preview["version"] != req.expected_version:
            return JSONResponse(status_code=409, content={"detail": "board version changed",
                "current_version": preview["version"], "cells": store_a.load(graph_id)})
        return preview
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/board/{graph_id}/merge", dependencies=[Depends(require_token)])
async def merge_nodes(graph_id: str, req: NodeMergeRequest):
    graph_id = _valid_path_meeting_id(graph_id)
    try:
        cells, receipt = store_a.merge_nodes(graph_id, req.duplicate_id, req.survivor_id,
            expected_version=req.expected_version, actor=req.actor, reason=req.reason)
    except VersionConflict as exc:
        return JSONResponse(status_code=409, content={"detail": "board version changed",
            "current_version": exc.current_version, "cells": exc.cells})
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _broadcast_board(graph_id)
    return {"ok": True, "version": store_a.version(graph_id), "cells": cells, "receipt": receipt}


@app.patch("/api/board/{graph_id}/nodes/{node_id}", dependencies=[Depends(require_token)])
async def patch_node(graph_id: str, node_id: str, req: NodePatchRequest):
    graph_id = _valid_path_meeting_id(graph_id or req.graph_id or config.CONFIG.default_meeting_id)
    try:
        cells = store_a.patch_node(graph_id, node_id, req.fields,
            expected_version=req.expected_version, actor=req.actor, reason=req.reason)
    except VersionConflict as exc:
        return JSONResponse(status_code=409, content={
            "detail": "board version changed", "current_version": exc.current_version,
            "cells": exc.cells,
        })
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _broadcast_board(graph_id)
    return {"ok": True, "graph_id": graph_id, "node_id": node_id,
            "version": store_a.version(graph_id), "cells": cells}


@app.post("/api/node/{node_id}/op", dependencies=[Depends(require_token)])
async def node_op(node_id: str, req: UserOpRequest):
    graph_id = _valid_path_meeting_id(req.graph_id or config.CONFIG.default_meeting_id)
    try:
        cells = store_a.apply_user_operation(graph_id, node_id, req.op,
                                             req.payload, req.actor,
                                             expected_version=req.expected_version)
    except VersionConflict as exc:
        return JSONResponse(status_code=409, content={"detail": "board version changed",
            "current_version": exc.current_version, "cells": exc.cells})
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _broadcast_board(graph_id)
    return {"ok": True, "graph_id": graph_id, "node_id": node_id,
            "op": req.op, "version": store_a.version(graph_id), "cells": cells}


class LockRequest(BaseModel):
    graph_id: str = ""
    locked: bool = True
    locked_by: str = "human"
    expected_version: int = Field(ge=0)


@app.post("/api/node/{node_id}/lock", dependencies=[Depends(require_token)])
async def node_lock(node_id: str, req: LockRequest):
    return await node_op(node_id, UserOpRequest(graph_id=req.graph_id, op="lock",
        payload={"locked": req.locked}, actor=req.locked_by,
        expected_version=req.expected_version))


class ImportanceRequest(BaseModel):
    graph_id: str = ""
    level: str = "normal"
    expected_version: int = Field(ge=0)


@app.post("/api/node/{node_id}/importance", dependencies=[Depends(require_token)])
async def node_importance(node_id: str, req: ImportanceRequest):
    return await node_op(node_id, UserOpRequest(graph_id=req.graph_id,
        op="set_importance", payload={"level": req.level},
        expected_version=req.expected_version))


class RollbackRequest(BaseModel):
    cell_id: str
    version: int
    expected_version: int = Field(ge=0)
    actor: str = "human"


@app.post("/api/board/{graph_id}/rollback", dependencies=[Depends(require_token)])
async def board_rollback(graph_id: str, req: RollbackRequest):
    graph_id = _valid_path_meeting_id(graph_id)
    try:
        cells = store_a.rollback_cell(graph_id, req.cell_id, req.version, actor=req.actor,
                                      expected_version=req.expected_version)
    except VersionConflict as exc:
        return JSONResponse(status_code=409, content={"detail": "board version changed",
            "current_version": exc.current_version, "cells": exc.cells})
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await bus.publish({"type": "board.rollback", "graph_id": graph_id,
                       "cell_id": req.cell_id, "version": store_a.version(graph_id), "cells": cells})
    await _broadcast_board(graph_id)
    return {"ok": True, "graph_id": graph_id, "cell_id": req.cell_id,
            "rolled_back_to": req.version, "version": store_a.version(graph_id), "cells": cells}
