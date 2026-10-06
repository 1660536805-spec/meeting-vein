"""Validate a normalized speech event at the board-server boundary."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import NormUtterance, SpeakerRef
from .storage import MEETING_ID_PATTERN, StoreA, StoreB

log = logging.getLogger("app.ingest")


class IncomingSpeaker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker_ref: str = Field(min_length=1, max_length=128)
    source_id: str | None = None
    display_name: str | None = None
    is_resolved: bool = False


class IncomingUtterance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    utterance_id: str = Field(min_length=1, max_length=128)
    meeting_id: str = Field(pattern=MEETING_ID_PATTERN)
    session_id: str | None = None
    seq: int = Field(ge=0)
    speaker: IncomingSpeaker
    text: str = Field(min_length=1, max_length=10000)
    language: str | None = None
    start_offset_ms: int = Field(ge=0)
    end_offset_ms: int = Field(ge=0)
    received_at_ms: int = Field(ge=0)
    is_final: Literal[True] = True
    is_partial: Literal[False] = False
    source: str = Field(min_length=1, max_length=64)
    translation: str | None = None
    raw_ref: str | None = None

    @model_validator(mode="after")
    def validate_content(self) -> "IncomingUtterance":
        self.text = self.text.strip()
        if not self.text or self.end_offset_ms < self.start_offset_ms:
            raise ValueError("invalid utterance content or time range")
        return self

    def to_domain(self, meta_id: str) -> NormUtterance:
        return NormUtterance(
            utterance_id=meta_id,
            meeting_id=self.meeting_id,
            session_id=self.session_id,
            seq=self.seq,
            speaker=SpeakerRef(**self.speaker.model_dump()),
            text=self.text,
            language=self.language,
            start_offset_ms=self.start_offset_ms,
            end_offset_ms=self.end_offset_ms,
            received_at_ms=self.received_at_ms,
            is_final=self.is_final,
            is_partial=self.is_partial,
            translation=self.translation,
            source=self.source,
            raw_ref=self.raw_ref,
        )


def metadata_id(meeting_id: str, utterance_id: str) -> str:
    digest = sha256(f"{meeting_id}\0{utterance_id}".encode("utf-8")).hexdigest()[:24]
    return f"utt_evt_{digest}"


Drive = Callable[..., Awaitable[None]]

MAX_LOCKS = 256


class UtteranceIngestor:
    """Store and process final utterances once per meeting and external ID.

    实时合批（P1②）：debounce_ms>0 时，窗口内到达的多句合并为一批，只付一次
    analyze+sync 的 LLM 调用；首句到达即排定 flush（leading schedule）。默认
    debounce_ms=0 保持逐句同步驱动，供 CLI/batch 调试与单测的确定性语义。
    """

    def __init__(self, store_a: StoreA, store_b: StoreB, drive: Drive, *,
                 debounce_ms: int = 0) -> None:
        self.store_a = store_a
        self.store_b = store_b
        self.drive = drive
        self._debounce_ms = max(0, int(debounce_ms))
        # 每会议一把锁；用 LRU 上限避免 meeting_id 无限增长导致字典泄漏。
        self._locks: "OrderedDict[str, asyncio.Lock]" = OrderedDict()
        # 合批窗口内待驱动的语句与定时器（仅 debounce_ms>0 时使用）。
        self._pending: dict[str, list[tuple[str, NormUtterance]]] = {}
        self._timers: dict[str, asyncio.Task] = {}

    def lock_for(self, meeting_id: str) -> asyncio.Lock:
        lock = self._locks.get(meeting_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[meeting_id] = lock
        self._locks.move_to_end(meeting_id)
        self._evict_locks(keep=meeting_id)
        return lock

    def _evict_locks(self, keep: str) -> None:
        """淘汰最久未使用且未被持有的锁，防止字典无界增长。"""
        while len(self._locks) > MAX_LOCKS:
            for mid in list(self._locks.keys()):
                if mid == keep:
                    continue
                lock = self._locks.get(mid)
                if lock is not None and lock.locked():
                    continue
                self._locks.pop(mid, None)
                break
            else:
                break  # 其余锁均在持有中，暂时放弃淘汰

    async def ingest(self, event: IncomingUtterance) -> dict:
        async with self.lock_for(event.meeting_id):
            meta_id = metadata_id(event.meeting_id, event.utterance_id)
            existing = self.store_b.get(meta_id)
            if existing and existing.get("processing_state") == "done":
                return {"ok": True, "utterance_id": event.utterance_id, "duplicate": True}
            if any(mid == meta_id for mid, _ in self._pending.get(event.meeting_id, [])):
                # 已在合批窗口内排队：不重复入队（仍待驱动，故非 done）。
                return {"ok": True, "utterance_id": event.utterance_id, "duplicate": True}
            if existing and any(
                meta_id in cell.get("data", {}).get("metadata_refs", [])
                for cell in self.store_a.load(event.meeting_id)
            ):
                self.store_b.put(meta_id, {**existing, "processing_state": "done"})
                return {"ok": True, "utterance_id": event.utterance_id, "duplicate": True}

            record = {
                "meta_id": meta_id,
                "kind": "utt",
                "meeting_id": event.meeting_id,
                "source_utterance_id": event.utterance_id,
                "text": event.text,
                "speaker_ref": event.speaker.speaker_ref,
                "start_offset_ms": event.start_offset_ms,
                "end_offset_ms": event.end_offset_ms,
                "source": event.source,
                "processing_state": "pending",
            }
            self.store_b.put(meta_id, record)

            if self._debounce_ms > 0:
                self._enqueue(event.meeting_id, meta_id, event.to_domain(meta_id))
                return {"ok": True, "utterance_id": event.utterance_id,
                        "duplicate": False, "batched": True}

            await self._finalize(event.meeting_id, [(meta_id, event.to_domain(meta_id))])
            return {"ok": True, "utterance_id": event.utterance_id, "duplicate": False}

    async def _finalize(self, meeting_id: str,
                        items: list[tuple[str, NormUtterance]]) -> None:
        """驱动一批语句并标记 done。同步语义：异常向上抛（幂等重试/补偿依赖）。"""
        await self.drive(meeting_id, raw_utterances=[u for _, u in items])
        # 提交点强制落盘：看板写盘默认按 SAVE_THROTTLE_MS 合并，
        # 但「已处理」标记一旦写下即不可回退，故须先保证图写入已持久化，
        # 否则崩溃重启后无法凭 board.metadata_refs 判定本条已消费（会重复计入）。
        self.store_a.flush(meeting_id, force=True)
        for meta_id, _ in items:
            rec = self.store_b.get(meta_id) or {"meta_id": meta_id}
            self.store_b.put(meta_id, {**rec, "processing_state": "done"})

    def _enqueue(self, meeting_id: str, meta_id: str, utt: NormUtterance) -> None:
        self._pending.setdefault(meeting_id, []).append((meta_id, utt))
        if meeting_id not in self._timers:
            window = max(0.01, self._debounce_ms / 1000.0)
            self._timers[meeting_id] = asyncio.create_task(
                self._flush_later(meeting_id, window))

    async def _flush_later(self, meeting_id: str, window: float) -> None:
        try:
            await asyncio.sleep(window)
        except asyncio.CancelledError:
            return
        await self._flush(meeting_id)

    async def _flush(self, meeting_id: str) -> None:
        items = self._pending.pop(meeting_id, None)
        timer = self._timers.pop(meeting_id, None)
        if timer and timer is not asyncio.current_task():
            timer.cancel()
        if not items:
            return
        async with self.lock_for(meeting_id):
            try:
                await self._finalize(meeting_id, items)
            except Exception as e:
                # 驱动失败：交由 _drive 的 P0① pending_batch 补偿队列重驱动。
                log.warning("[ingest] batched flush failed for %s: %r", meeting_id, e)

    async def flush_all(self) -> None:
        for meeting_id in list(self._pending.keys()):
            await self._flush(meeting_id)
