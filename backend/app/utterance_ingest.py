"""Validate a normalized speech event at the board-server boundary."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from hashlib import sha256
from typing import Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import NormUtterance, SpeakerRef
from .storage import MEETING_ID_PATTERN, StoreA, StoreB
from .errors import safe_error_code

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
StatusChanged = Callable[[dict], Awaitable[None]]

MAX_LOCKS = 256


class UtteranceIngestor:
    """Store and process final utterances once per meeting and external ID.

    实时合批（P1②）：debounce_ms>0 时，窗口内到达的多句合并为一批，只付一次
    analyze+sync 的 LLM 调用；首句到达即排定 flush（leading schedule）。默认
    debounce_ms=0 保持逐句同步驱动，供 CLI/batch 调试与单测的确定性语义。
    """

    def __init__(self, store_a: StoreA, store_b: StoreB, drive: Drive, *,
                 debounce_ms: int = 0, status_changed: StatusChanged | None = None) -> None:
        self.store_a = store_a
        self.store_b = store_b
        self.drive = drive
        self.status_changed = status_changed
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
                return self._response(event, meta_id, existing, duplicate=True)
            if any(mid == meta_id for mid, _ in self._pending.get(event.meeting_id, [])):
                # 已在合批窗口内排队：不重复入队（仍待驱动，故非 done）。
                return self._response(event, meta_id, existing or {"processing_state": "pending"}, duplicate=True)
            if existing and any(
                meta_id in cell.get("data", {}).get("metadata_refs", [])
                for cell in self.store_a.load(event.meeting_id)
            ):
                self.store_b.put(meta_id, {**existing, "processing_state": "done", "updated_at_ms": self._now_ms()})
                self.store_b.flush(force=True)
                return self._response(event, meta_id, self.store_b.get(meta_id) or existing, duplicate=True)

            record = {
                **(existing or {}),
                "meta_id": meta_id,
                "kind": "utt",
                "meeting_id": event.meeting_id,
                "source_utterance_id": event.utterance_id,
                "session_id": event.session_id,
                "seq": event.seq,
                "text": event.text,
                "speaker_ref": event.speaker.speaker_ref,
                "display_name": event.speaker.display_name,
                "is_resolved": event.speaker.is_resolved,
                "language": event.language,
                "start_offset_ms": event.start_offset_ms,
                "end_offset_ms": event.end_offset_ms,
                "received_at_ms": event.received_at_ms,
                "source": event.source,
                "raw_ref": event.raw_ref,
                "processing_state": "pending",
                "attempts": int((existing or {}).get("attempts") or 0),
                "last_error": None,
                "updated_at_ms": self._now_ms(),
            }
            self.store_b.put(meta_id, record)
            self.store_b.flush(force=True)
            await self._publish_status(event.meeting_id, meta_id, record)

            if self._debounce_ms > 0:
                self._enqueue(event.meeting_id, meta_id, event.to_domain(meta_id))
                return self._response(event, meta_id, record, duplicate=False, batched=True)

            try:
                await self._finalize(event.meeting_id, [(meta_id, event.to_domain(meta_id))])
            except Exception:
                pass  # The durable failed state and last_error are returned and can be retried.
            return self._response(event, meta_id, self.store_b.get(meta_id) or record, duplicate=False)

    @staticmethod
    def _now_ms() -> int:
        import time
        return int(time.time() * 1000)

    def _response(self, event: IncomingUtterance, meta_id: str, record: dict, *,
                  duplicate: bool, batched: bool = False) -> dict:
        state = {"done": "committed", "processing": "processing", "failed": "failed"}.get(
            record.get("processing_state"), "accepted")
        result = {
            "ok": True, "utterance_id": event.utterance_id, "duplicate": duplicate,
            "state": state, "meta_id": meta_id,
            "board_version": self.store_a.version(event.meeting_id) if state == "committed" else None,
            "board_effect": self.board_effect(event.meeting_id, meta_id) if state == "committed" else None,
        }
        if batched:
            result["batched"] = True
        if record.get("last_error"):
            result["error"] = str(record["last_error"])
        return result

    def board_effect(self, meeting_id: str, meta_id: str) -> str:
        """A completed pipeline is not proof that this utterance changed the board."""
        linked = any(meta_id in (cell.get("data") or {}).get("metadata_refs", [])
                     for cell in self.store_a.load(meeting_id))
        return "linked" if linked else "unlinked"

    async def _publish_status(self, meeting_id: str, meta_id: str, record: dict) -> None:
        if self.status_changed is None:
            return
        await self.status_changed({
            "type": "utterance.status", "meeting_id": meeting_id, "meta_id": meta_id,
            "utterance_id": record.get("source_utterance_id"),
            "state": {"done": "committed", "processing": "processing", "failed": "failed"}.get(
                record.get("processing_state"), "accepted"),
            "attempts": int(record.get("attempts") or 0),
            "last_error": record.get("last_error"),
            "board_effect": self.board_effect(meeting_id, meta_id)
            if record.get("processing_state") == "done" else None,
        })

    async def _finalize(self, meeting_id: str,
                        items: list[tuple[str, NormUtterance]]) -> None:
        """驱动一批语句并标记 done。同步语义：异常向上抛（幂等重试/补偿依赖）。"""
        for meta_id, _ in items:
            rec = self.store_b.get(meta_id) or {"meta_id": meta_id}
            processing = {**rec, "processing_state": "processing", "attempts": int(rec.get("attempts") or 0) + 1,
                          "last_error": None, "updated_at_ms": self._now_ms()}
            self.store_b.put(meta_id, processing)
            self.store_b.flush(force=True)
            await self._publish_status(meeting_id, meta_id, processing)
        try:
            result = await self.drive(meeting_id, raw_utterances=[u for _, u in items])
            error = result.get("error") if isinstance(result, dict) else None
            if error:
                raise RuntimeError(str(error))
            # Persist the graph before marking raw inputs committed.
            self.store_a.flush(meeting_id, force=True)
        except Exception as exc:
            for meta_id, _ in items:
                rec = self.store_b.get(meta_id) or {"meta_id": meta_id}
                failed = {**rec, "processing_state": "failed", "last_error": safe_error_code(exc),
                          "updated_at_ms": self._now_ms()}
                self.store_b.put(meta_id, failed)
                await self._publish_status(meeting_id, meta_id, failed)
            self.store_b.flush(force=True)
            raise
        for meta_id, _ in items:
            rec = self.store_b.get(meta_id) or {"meta_id": meta_id}
            committed = {**rec, "processing_state": "done", "last_error": None, "updated_at_ms": self._now_ms()}
            self.store_b.put(meta_id, committed)
            await self._publish_status(meeting_id, meta_id, committed)
        self.store_b.flush(force=True)

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
                log.warning("[ingest] batched flush failed for %s (%s)", meeting_id, safe_error_code(e))

    async def flush_all(self) -> None:
        for meeting_id in list(self._pending.keys()):
            await self._flush(meeting_id)

    async def recover_incomplete(self) -> int:
        """Replay durable accepted/processing/failed records once after a restart."""
        records = self.store_b.list_by_kind("utt")
        batched = {mid for batch in self.store_b.list_by_kind("pending_batch")
                   for mid in batch.get("utterance_ids") or []}
        recovered = 0
        for record in records:
            if record.get("processing_state") not in {"pending", "processing", "failed"}:
                continue
            meta_id = record.get("meta_id")
            meeting_id = record.get("meeting_id")
            if not meta_id or not meeting_id or meta_id in batched:
                continue
            if any(meta_id in (cell.get("data") or {}).get("metadata_refs", [])
                   for cell in self.store_a.load(meeting_id)):
                # A crash may occur after the board commit but before Store B's done
                # marker. Repair the marker without driving the same utterance twice.
                committed = {**record, "processing_state": "done", "last_error": None,
                             "updated_at_ms": self._now_ms()}
                self.store_b.put(meta_id, committed)
                self.store_b.flush(force=True)
                await self._publish_status(meeting_id, meta_id, committed)
                recovered += 1
                continue
            speaker = SpeakerRef(
                speaker_ref=record.get("speaker_ref") or "unknown",
                display_name=record.get("display_name"),
                is_resolved=bool(record.get("is_resolved", False)),
            )
            utterance = NormUtterance(
                utterance_id=meta_id, meeting_id=meeting_id, session_id=record.get("session_id"),
                seq=int(record.get("seq") or 0), speaker=speaker, text=record.get("text") or "",
                language=record.get("language"), start_offset_ms=int(record.get("start_offset_ms") or 0),
                end_offset_ms=int(record.get("end_offset_ms") or 0),
                received_at_ms=int(record.get("received_at_ms") or self._now_ms()),
                is_final=True, is_partial=False, source=record.get("source") or "recovery",
                raw_ref=record.get("raw_ref"),
            )
            try:
                await self._finalize(meeting_id, [(meta_id, utterance)])
            except Exception as exc:
                log.warning("[ingest] recovery failed for %s (%s)", meta_id, safe_error_code(exc))
            recovered += 1
        return recovered
