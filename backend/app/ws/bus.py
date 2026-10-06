"""WS 事件总线（Design_CursorCapture §4 / DataFlow 传输层）。

MVP 用 asyncio 队列实现进程内广播；生产可换 fastapi/websockets 多连接。
事件类型：asr.event / cursor.event（上行）/ mascot_state / board.update（下行）。

背压策略：每订阅者队列有界（MAX_QUEUE）。满了说明消费者（浏览器）跟不上，
丢弃最旧事件保证内存不无界增长；board.update 是「最新快照」语义，允许直接丢旧帧。
"""
from __future__ import annotations
import asyncio
from typing import List

MAX_QUEUE = 64


class EventBus:
    def __init__(self, max_queue: int = MAX_QUEUE):
        self._subs: List[asyncio.Queue] = []
        self._max_queue = max_queue

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=self._max_queue)
        self._subs.append(q)
        return q

    async def publish(self, event: dict) -> None:
        for q in list(self._subs):
            self._put(q, event)

    @staticmethod
    def _put(q: asyncio.Queue, event: dict) -> None:
        """非阻塞投递：队列满时丢最旧帧，避免慢消费者拖垮发布方与内存。"""
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            try:
                q.get_nowait()          # 丢最旧
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subs:
            self._subs.remove(q)
