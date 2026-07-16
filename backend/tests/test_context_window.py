from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.graph.context_window import build_context_window
from app.graph.model import build_model_messages


def test_context_window_keeps_all_messages_when_under_budget():
    system = [SystemMessage(content="system")]
    history = [
        HumanMessage(content="hello"),
        AIMessage(content="hi"),
    ]

    window = build_context_window(system, history, max_tokens=1_000)

    assert window.messages == [*system, *history]
    assert window.dropped_messages == 0
    assert window.overflowed is False


def test_context_window_drops_old_turns_without_splitting_tool_protocol():
    call = {
        "id": "call-weather",
        "name": "get_weather",
        "args": {"city": "Shanghai"},
        "type": "tool_call",
    }
    old_turn = [
        HumanMessage(content="old question " * 400),
        AIMessage(content="old answer " * 400),
    ]
    current_turn = [
        HumanMessage(content="weather now?"),
        AIMessage(content="", tool_calls=[call]),
        ToolMessage(
            content='{"temperature": 30}',
            tool_call_id="call-weather",
            name="get_weather",
        ),
    ]

    window = build_context_window(
        [SystemMessage(content="system")],
        [*old_turn, *current_turn],
        max_tokens=200,
    )

    assert window.messages[1:] == current_turn
    assert window.dropped_messages == len(old_turn)
    assert window.overflowed is False


def test_context_window_keeps_latest_turn_and_reports_single_turn_overflow():
    current_turn = [HumanMessage(content="large request " * 1_000)]

    window = build_context_window(
        [SystemMessage(content="system")],
        current_turn,
        max_tokens=100,
    )

    assert window.messages[-1] is current_turn[0]
    assert window.dropped_messages == 0
    assert window.overflowed is True


def test_context_window_rejects_non_positive_budget():
    try:
        build_context_window([], [], max_tokens=0)
    except ValueError as exc:
        assert str(exc) == "max_tokens must be greater than zero"
    else:
        raise AssertionError("expected ValueError")


def test_model_input_includes_compacted_summary_and_session_memory():
    messages = build_model_messages(
        {
            "messages": [HumanMessage(content="current")],
            "model_id": "deepseek-v4-flash",
            "system_prompt": "",
            "user_id": "user",
            "conversation_id": "conversation",
            "supervisor_decision": None,
            "active_agent": None,
            "completed_agents": [],
            "worker_result": "",
            "source_citations": [],
            "context_summary": "Earlier decision: use SQLite",
            "session_memory": "User prefers concise Chinese answers",
            "session_memory_cursor": "old-message",
            "context_report": None,
            "error": None,
        },
        ["node prompt"],
    )

    system_text = "\n".join(
        str(message.content)
        for message in messages
        if isinstance(message, SystemMessage)
    )
    assert "Earlier decision: use SQLite" in system_text
    assert "User prefers concise Chinese answers" in system_text
    assert messages[-1].content == "current"
