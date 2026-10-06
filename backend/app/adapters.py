"""适配层：把原始平台事件归一化为统一输入流。

- asr_adapter  : 腾讯会议 asr-push / FunASR sentence_info → NormUtterance
- cursor_adapter: 原始 X6 事件 → NormCursorEvent

Adapter 与存储/编排解耦：新增后端只加一个 Adapter（ASR_UnifiedSchema §3 / Research_CursorIntent_Capture §3）。
"""
from __future__ import annotations
import itertools
import time
import uuid
from typing import Optional

from .models import NormUtterance, NormCursorEvent, SpeakerRef, ActorRef, CursorTarget


# ---------------------------------------------------------------------------
# ASR Adapter
# ---------------------------------------------------------------------------
_SEQ_COUNTER = itertools.count(int(time.time() * 1000))


def _seq() -> int:
    # 进程内单调递增：以毫秒时间戳为起点，同毫秒内多次调用也严格递增
    # （旧实现 %(10**9) 会在毫秒回绕处产生非单调值；生产由统一入口分配，Design §2.4 Q1）
    return next(_SEQ_COUNTER)


def from_tencent_asr_push(meeting_id: str, payload: dict) -> NormUtterance:
    """腾讯会议 meeting.asr-push 单句 → NormUtterance。

    payload 关键字段（ASR_UnifiedSchema §2）：
      speaker.userid / ms_open_id / nickname；speech_time(ms)；sid；content.text；translate
    """
    sp = payload.get("speaker", {})
    speaker_ref = sp.get("userid") or sp.get("ms_open_id") or "ms:anonymous"
    is_resolved = bool(sp.get("userid"))
    return NormUtterance(
        utterance_id=payload.get("sid") or f"utt_{uuid.uuid4().hex[:12]}",
        meeting_id=meeting_id, session_id=None, seq=_seq(),
        speaker=SpeakerRef(
            speaker_ref=f"ms:{speaker_ref}",
            source_id=sp.get("ms_open_id"),
            display_name=sp.get("nickname"),
            is_resolved=is_resolved,
        ),
        text=payload.get("content", {}).get("text", ""),
        language="zh",
        start_offset_ms=int(payload.get("speech_time", 0)),
        end_offset_ms=int(payload.get("speech_time", 0)),
        received_at_ms=int(time.time() * 1000),
        is_final=True, is_partial=False,
        translation=payload.get("content", {}).get("translate"),
        source="tencent_meeting", raw_ref=f"tencent:sid={payload.get('sid')}",
    )


def from_funasr_sentence_info(meeting_id: str, info: dict, *,
                              utterance_id: Optional[str] = None) -> NormUtterance:
    """FunASR sentence_info → NormUtterance（说话人为匿名 spk 编号，Research_FunASR_ASR §3）。

    utterance_id：可传确定性 id（如 {spk}-{start_ms}），配合服务端幂等去重，
    使流式重连/超时重发不会把同一句重复灌入看板；缺省仍生成随机 id。
    """
    spk = info.get("spk", "0")
    return NormUtterance(
        utterance_id=utterance_id or f"utt_{uuid.uuid4().hex[:12]}",
        meeting_id=meeting_id, session_id=None, seq=_seq(),
        speaker=SpeakerRef(speaker_ref=f"spk:{spk}", source_id=spk,
                            display_name=f"说话人{spk}", is_resolved=False),
        text=info.get("sentence", ""), language="zh",
        start_offset_ms=int(info.get("start", 0) * 1000),
        end_offset_ms=int(info.get("end", 0) * 1000),
        received_at_ms=int(time.time() * 1000),
        is_final=True, is_partial=False, source="funasr",
        raw_ref=f"funasr:spk={spk}",
    )


# ---------------------------------------------------------------------------
# CLI Debug Adapter（输入接口调试口）
# ---------------------------------------------------------------------------
def from_stored_record(meeting_id: str, rec: dict) -> NormUtterance:
    """Store B 记录 → NormUtterance（LLM 失败批次重驱动用，P0①）。

    从持久化记录重建归一化语句，使失败批次可原样重放入编排器；
    utterance_id 沿用原 id，重放后仍受服务端幂等去重保护。
    """
    return NormUtterance(
        utterance_id=rec.get("meta_id") or f"utt_{uuid.uuid4().hex[:12]}",
        meeting_id=meeting_id, session_id=None, seq=_seq(),
        speaker=SpeakerRef(speaker_ref=rec.get("speaker_ref") or "unknown",
                            source_id=None, display_name=None, is_resolved=False),
        text=rec.get("text", ""), language="zh",
        start_offset_ms=int(rec.get("start_offset_ms") or 0),
        end_offset_ms=int(rec.get("end_offset_ms") or 0),
        received_at_ms=int(time.time() * 1000),
        is_final=True, is_partial=False,
        source=rec.get("source") or "replay", raw_ref="store_b:replay",
    )


def from_cli_text(meeting_id: str, text: str, *,
                  speaker_ref: str = "cli:user",
                  display_name: str = "CLI调试",
                  is_resolved: bool = True,
                  source: str = "cli_debug",
                  utterance_id: Optional[str] = None) -> NormUtterance:
    """CLI 调试口：把一行键入文本归一化为 NormUtterance（与腾讯会议 / FunASR 对等流入编排器）。

    用于本地/offline 调试双 Agent 管线，无需真实 ASR：
      - 可作为独立输入源（server.POST /api/cli/push 与 cli_debug.py 均走此适配器）
      - 若需模拟多人，传不同 speaker_ref（如 cli:user_zhang）即可
      - utterance_id：客户端可传确定性 id（如按行号编号），配合服务端幂等去重，
        使网络超时重发不会把同一句话重复灌入看板；缺省仍生成随机 id。
    """
    text = (text or "").strip()
    return NormUtterance(
        utterance_id=utterance_id or f"utt_cli_{uuid.uuid4().hex[:12]}",
        meeting_id=meeting_id, session_id=None, seq=_seq(),
        speaker=SpeakerRef(speaker_ref=speaker_ref, source_id=None,
                            display_name=display_name, is_resolved=is_resolved),
        text=text, language="zh",
        start_offset_ms=0, end_offset_ms=0, received_at_ms=int(time.time() * 1000),
        is_final=True, is_partial=False, source=source,
        raw_ref=f"cli:{speaker_ref}",
    )


# ---------------------------------------------------------------------------
# Cursor Adapter
# ---------------------------------------------------------------------------
def from_x6_event(meeting_id: str, raw: dict) -> NormCursorEvent:
    """原始 X6 事件 → NormCursorEvent。

    raw 形如 {"event":"node:mouseup", "node_id":"n_issue_q3", "x":412, "y":268,
             "gesture_type":"drag_node", "is_final":true, ...}
    前端已完成拖拽 partial→final 收口（Design_CursorCapture §2.3），此处仅做单事件映射。
    """
    target = raw.get("target", {})
    return NormCursorEvent(
        event_id=raw.get("event_id") or f"c_{uuid.uuid4().hex[:12]}",
        meeting_id=meeting_id, session_id=None, seq=_seq(),
        actor=ActorRef(user_ref=raw.get("actor", "local:user"), device=raw.get("device", "desktop")),
        gesture_type=raw.get("gesture_type", "hover"),
        target=CursorTarget(node_id=target.get("node_id"), node_type=target.get("node_type"),
                             edge_id=target.get("edge_id")),
        pointer={"x": raw.get("x", 0), "y": raw.get("y", 0)},
        intent_hint=raw.get("intent_hint"),
        start_offset_ms=int(raw.get("start_offset_ms", 0)),
        end_offset_ms=int(raw.get("end_offset_ms", 0)),
        received_at_ms=int(time.time() * 1000),
        is_final=raw.get("is_final", True),
        is_partial=raw.get("is_partial", False),
        source="x6_canvas", raw_ref=raw.get("raw_ref"),
    )
