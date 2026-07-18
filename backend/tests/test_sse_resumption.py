import json

import pytest

from app.streaming import ResumableSSEStream, SSEStreamRegistry


def decode_sse(chunk: bytes) -> tuple[int | None, dict]:
    event_id = None
    data_lines: list[str] = []
    for line in chunk.decode().splitlines():
        if line.startswith("id: "):
            event_id = int(line[4:])
        elif line.startswith("data: "):
            data_lines.append(line[6:])
    return event_id, json.loads("\n".join(data_lines))


@pytest.mark.asyncio
async def test_resumable_stream_replays_only_events_after_last_event_id():
    stream = ResumableSSEStream("stream-1", "user-1")
    await stream.publish({"type": "text_start", "messageId": "message-1"})
    await stream.publish({"type": "text_delta", "messageId": "message-1", "delta": "你"})

    initial = stream.subscribe()
    first = decode_sse(await anext(initial))
    second = decode_sse(await anext(initial))
    await initial.aclose()
    assert [first[0], second[0]] == [1, 2]

    await stream.publish({"type": "text_delta", "messageId": "message-1", "delta": "好"})
    await stream.publish({"type": "done", "messageId": "message-1"})

    resumed = [decode_sse(chunk) async for chunk in stream.subscribe(after_event_id=2)]
    assert [event_id for event_id, _payload in resumed] == [3, 4]
    assert "".join(
        payload.get("delta", "") for _event_id, payload in [second, *resumed]
    ) == "你好"
    assert resumed[-1][1]["type"] == "done"


@pytest.mark.asyncio
async def test_resumable_stream_reports_expired_replay_window():
    stream = ResumableSSEStream("stream-1", "user-1", max_events=2)
    await stream.publish({"type": "text_delta", "delta": "1"})
    await stream.publish({"type": "text_delta", "delta": "2"})
    await stream.publish({"type": "text_delta", "delta": "3"})

    chunks = [chunk async for chunk in stream.subscribe(after_event_id=0)]
    _event_id, payload = decode_sse(chunks[0])
    assert payload["type"] == "error"
    assert payload["code"] == "STREAM_REPLAY_EXPIRED"


@pytest.mark.asyncio
async def test_stream_registry_preserves_owner_isolation():
    registry = SSEStreamRegistry(retention_seconds=1)
    stream = ResumableSSEStream("shared-id", "owner-a")
    registry.register(stream)

    assert registry.get("shared-id", "owner-a") is stream
    assert registry.get("shared-id", "owner-b") is None
    assert registry.has("shared-id")
    await registry.close()
