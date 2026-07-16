import asyncio
from datetime import datetime, timedelta, timezone

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.graph.message import add_messages

from app.graph import context_manager
from app.graph.context_manager import ContextPolicy, manage_context


NOW = datetime(2026, 7, 16, 12, tzinfo=timezone.utc)


def state(messages, **overrides):
    value = {
        "messages": messages,
        "model_id": "deepseek-v4-flash",
        "system_prompt": "",
        "user_id": "user",
        "conversation_id": "conversation",
        "supervisor_decision": None,
        "active_agent": None,
        "completed_agents": [],
        "worker_result": "",
        "source_citations": [],
        "context_summary": "",
        "session_memory": "",
        "session_memory_cursor": "",
        "context_report": None,
        "error": None,
    }
    value.update(overrides)
    return value


def noop_writer(_event):
    return None


def turn(index: int, words: int = 40):
    return [
        HumanMessage(content=(f"question-{index} " * words), id=f"h-{index}"),
        AIMessage(content=(f"answer-{index} " * words), id=f"a-{index}"),
    ]


def test_microcompact_expires_tool_payload_without_breaking_protocol():
    call = {
        "id": "call-1",
        "name": "calculate",
        "args": {"expression": "1+1"},
        "type": "tool_call",
    }
    messages = [
        HumanMessage(content="calculate", id="human"),
        AIMessage(content="", tool_calls=[call], id="assistant-call"),
        ToolMessage(
            content='{"large":"payload"}',
            tool_call_id="call-1",
            name="calculate",
            id="tool-result",
            additional_kwargs={
                "context_created_at": (NOW - timedelta(hours=2)).isoformat(),
            },
        ),
        AIMessage(content="result is 2", id="answer"),
        HumanMessage(content="continue", id="current"),
    ]

    update = asyncio.run(manage_context(
        state(messages),
        writer=noop_writer,
        policy=ContextPolicy(max_tokens=10_000, microcompact_ttl_seconds=60),
        now=NOW,
    ))

    replacement = next(
        message for message in update["messages"]
        if isinstance(message, ToolMessage)
    )
    assert replacement.id == "tool-result"
    assert replacement.tool_call_id == "call-1"
    assert "TTL" in str(replacement.content)
    assert update["context_report"]["strategies"] == ["microcompact"]


def test_context_collapse_updates_summary_and_session_memory(monkeypatch):
    async def fake_summary(*_args):
        return "rolling summary", "project memory"

    monkeypatch.setattr(context_manager, "summarize_context", fake_summary)
    messages = [message for index in range(5) for message in turn(index, 100)]
    events = []
    update = asyncio.run(manage_context(
        state(messages),
        writer=events.append,
        policy=ContextPolicy(
            max_tokens=10_000,
            session_memory_ratio=0.01,
            collapse_ratio=0.02,
            full_compact_ratio=0.9,
            ptl_truncation_ratio=1.0,
            keep_recent_turns=2,
        ),
        now=NOW,
    ))

    assert update["context_summary"] == "rolling summary"
    assert update["session_memory"] == "project memory"
    assert update["session_memory_cursor"] == "a-0"
    assert update["context_report"]["strategies"] == [
        "context_collapse",
        "session_memory",
    ]
    assert any(isinstance(message, RemoveMessage) for message in update["messages"])
    assert events[0]["type"] == "context_status"


def test_full_compact_then_ptl_keeps_latest_complete_turn(monkeypatch):
    async def fake_summary(*_args):
        return "short", "memory"

    monkeypatch.setattr(context_manager, "summarize_context", fake_summary)
    messages = [message for index in range(4) for message in turn(index, 500)]
    update = asyncio.run(manage_context(
        state(messages),
        writer=noop_writer,
        policy=ContextPolicy(
            max_tokens=200,
            session_memory_ratio=0.01,
            collapse_ratio=0.02,
            full_compact_ratio=0.03,
            ptl_truncation_ratio=0.04,
            keep_recent_turns=2,
        ),
        now=NOW,
    ))

    removed = {
        str(message.id)
        for message in update["messages"]
        if isinstance(message, RemoveMessage)
    }
    assert {"h-0", "a-0", "h-1", "a-1", "h-2", "a-2"} <= removed
    assert "h-3" not in removed and "a-3" not in removed
    assert update["context_report"]["strategies"] == [
        "session_memory",
        "full_compact",
        "ptl_truncation",
    ]
    reduced = add_messages(messages, update["messages"])
    assert [(message.type, message.id) for message in reduced] == [
        ("human", "h-3"),
        ("ai", "a-3"),
    ]


def test_context_policy_rejects_out_of_order_thresholds():
    try:
        ContextPolicy(session_memory_ratio=0.8, collapse_ratio=0.5)
    except ValueError as exc:
        assert "ordered" in str(exc)
    else:
        raise AssertionError("expected ValueError")
