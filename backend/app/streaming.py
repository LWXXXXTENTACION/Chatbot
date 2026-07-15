"""Shared helpers for reliable server-sent event delivery."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any


async def stream_sse_events(
    event_queue: asyncio.Queue[dict[str, Any] | None],
    graph_task: asyncio.Task[Any],
    *,
    keepalive_seconds: float = 60.0,
) -> AsyncIterator[bytes]:
    """Yield queued events through the terminal event, then clean up the producer."""
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

            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
            if event.get("type") in {"done", "error"}:
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
