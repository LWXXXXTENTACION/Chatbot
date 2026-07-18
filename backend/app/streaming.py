"""可靠 SSE 传输层。

这一层不理解 LangGraph 的业务状态，只负责把图产生的事件可靠地送到浏览器：

1. ``publish`` 为事件分配单调递增的 id，并写入有界内存日志；
2. ``subscribe`` 根据 ``Last-Event-ID`` 回放缺失事件，再等待新事件；
3. HTTP 连接断开只会结束一个订阅者，不会取消后台 Graph 生产任务；
4. 只有显式停止、应用退出或任务结束，生产任务才会被回收。

把“Graph 执行”和“HTTP 连接寿命”解耦，是刷新续传和切换对话不重复生成的关键。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any


TERMINAL_EVENT_TYPES = {"done", "error"}


@dataclass(frozen=True, slots=True)
class SSEEventRecord:
    event_id: int
    payload: dict[str, Any]

    def encode(self) -> bytes:
        """按 SSE 标准编码；JSON 保留中文，最后统一转成 UTF-8 字节。"""
        data = json.dumps(self.payload, ensure_ascii=False)
        return f"id: {self.event_id}\ndata: {data}\n\n".encode("utf-8")


class ResumableSSEStream:
    """一个 ``stream_id`` 对应的一次 Graph 运行和它的有界事件日志。"""

    def __init__(
        self,
        stream_id: str,
        owner_id: str,
        *,
        max_events: int = 20_000,
    ) -> None:
        self.stream_id = stream_id
        self.owner_id = owner_id
        self._events: dict[int, SSEEventRecord] = {}
        self._max_events = max_events
        self._earliest_event_id = 1
        self._condition = asyncio.Condition()
        self._next_event_id = 1
        self._terminal = False
        self.completed_at: float | None = None
        self.producer_task: asyncio.Task[Any] | None = None

    def attach_producer(self, task: asyncio.Task[Any]) -> None:
        self.producer_task = task

    async def publish(self, payload: dict[str, Any]) -> int:
        """原子地追加事件，并唤醒所有正在等待的首连/续传订阅者。"""
        async with self._condition:
            if self._terminal:
                return self._next_event_id - 1
            event_id = self._next_event_id
            self._next_event_id += 1
            if len(self._events) >= self._max_events:
                # 日志必须有上限；过旧游标会收到明确的“续传窗口过期”，
                # 不能悄悄跳过内容，否则前端会得到看似完整但实际缺段的回答。
                self._events.pop(self._earliest_event_id, None)
                self._earliest_event_id += 1
            self._events[event_id] = SSEEventRecord(event_id, payload)
            if payload.get("type") in TERMINAL_EVENT_TYPES:
                self._terminal = True
                self.completed_at = time.monotonic()
            self._condition.notify_all()
            return event_id

    async def subscribe(
        self,
        after_event_id: int = 0,
        *,
        keepalive_seconds: float = 15.0,
    ) -> AsyncIterator[bytes]:
        """从游标之后开始订阅；先回放历史，再实时等待后续事件。"""
        cursor = max(0, after_event_id)
        while True:
            timed_out = False
            replay_expired = False
            records: list[SSEEventRecord] = []
            terminal = False

            async with self._condition:
                if self._events and cursor < self._earliest_event_id - 1:
                    replay_expired = True
                else:
                    records = self._records_after(cursor)
                    terminal = self._terminal
                    if not records and not terminal:
                        try:
                            await asyncio.wait_for(
                                self._condition.wait(),
                                timeout=keepalive_seconds,
                            )
                        except asyncio.TimeoutError:
                            timed_out = True
                        if self._events and cursor < self._earliest_event_id - 1:
                            replay_expired = True
                        else:
                            records = self._records_after(cursor)
                            terminal = self._terminal

            if replay_expired:
                payload = {
                    "type": "error",
                    "message": "续传窗口已过期，请重新发送消息",
                    "code": "STREAM_REPLAY_EXPIRED",
                }
                yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
                return

            if timed_out and not records:
                # 注释行不会触发业务事件，但能穿过代理并让前端看门狗确认连接存活。
                yield b": keepalive\n\n"
                continue

            for record in records:
                yield record.encode()
                cursor = record.event_id
                if record.payload.get("type") in TERMINAL_EVENT_TYPES:
                    return

            if terminal and not records:
                return

    async def cancel(self) -> None:
        task = self.producer_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _records_after(self, event_id: int) -> list[SSEEventRecord]:
        start = max(event_id + 1, self._earliest_event_id)
        return [
            self._events[index]
            for index in range(start, self._next_event_id)
            if index in self._events
        ]


class SSEStreamRegistry:
    """应用级流注册表；终态日志保留一段时间，供刷新后的浏览器补读。"""

    def __init__(self, *, retention_seconds: float = 300.0) -> None:
        self._streams: dict[str, ResumableSSEStream] = {}
        self._retention_seconds = retention_seconds
        self._reaper_task: asyncio.Task[Any] | None = None

    def start(self) -> None:
        if self._reaper_task is None:
            self._reaper_task = asyncio.create_task(self._reap_loop())

    def get(self, stream_id: str, owner_id: str) -> ResumableSSEStream | None:
        # 同时校验 owner，避免用户猜到 stream_id 后读取别人的事件流。
        self._prune()
        stream = self._streams.get(stream_id)
        if stream is None or stream.owner_id != owner_id:
            return None
        return stream

    def has(self, stream_id: str) -> bool:
        self._prune()
        return stream_id in self._streams

    def register(self, stream: ResumableSSEStream) -> None:
        self._prune()
        if stream.stream_id in self._streams:
            raise ValueError("stream_id already exists")
        self._streams[stream.stream_id] = stream

    async def close(self) -> None:
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
            self._reaper_task = None
        streams = list(self._streams.values())
        self._streams.clear()
        await asyncio.gather(*(stream.cancel() for stream in streams))

    def _prune(self) -> None:
        now = time.monotonic()
        expired = [
            stream_id
            for stream_id, stream in self._streams.items()
            if stream.completed_at is not None
            and now - stream.completed_at >= self._retention_seconds
        ]
        for stream_id in expired:
            self._streams.pop(stream_id, None)

    async def _reap_loop(self) -> None:
        interval = max(1.0, min(60.0, self._retention_seconds))
        while True:
            await asyncio.sleep(interval)
            self._prune()


async def stream_sse_events(
    event_queue: asyncio.Queue[dict[str, Any] | None],
    graph_task: asyncio.Task[Any],
    *,
    keepalive_seconds: float = 60.0,
) -> AsyncIterator[bytes]:
    """旧队列模式的兼容助手；新聊天接口使用 ``ResumableSSEStream``。"""
    try:
        while True:
            try:
                event = await asyncio.wait_for(
                    event_queue.get(),
                    timeout=keepalive_seconds,
                )
            except asyncio.TimeoutError:
                yield b": keepalive\n\n"
                continue

            if event is None:
                break

            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")
            if event.get("type") in TERMINAL_EVENT_TYPES:
                break
    finally:
        if not graph_task.done():
            graph_task.cancel()
            try:
                await graph_task
            except asyncio.CancelledError:
                pass

        while not event_queue.empty():
            event_queue.get_nowait()
