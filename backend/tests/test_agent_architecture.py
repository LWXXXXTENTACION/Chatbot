import asyncio
import json

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from app.graph import nodes
from app.graph.builder import build_graph
from app.graph.deep_search import dedupe_sources, parse_search_queries
from app.tools import DEEP_SEARCH_TOOLS, MAIN_AGENT_TOOLS


def base_state(**overrides):
    state = {
        "messages": [HumanMessage(content="test")],
        "model_id": "deepseek-v4-flash",
        "system_prompt": "",
        "user_id": "user",
        "conversation_id": "conversation",
        "source_citations": [],
        "retrieved_docs": "",
        "search_iteration": 0,
        "search_history": [],
        "error": None,
    }
    state.update(overrides)
    return state


def test_graph_has_one_main_agent_and_one_tool_node():
    graph = build_graph().get_graph()
    assert set(graph.nodes) == {"__start__", "main_agent", "tools", "__end__"}


def test_main_agent_cannot_call_raw_web_search():
    assert [tool.name for tool in MAIN_AGENT_TOOLS] == [
        "get_weather",
        "calculate",
        "create_artifact",
        "deep_search",
    ]
    assert [tool.name for tool in DEEP_SEARCH_TOOLS] == ["web_search"]


def test_query_parser_and_source_deduplication_are_bounded():
    assert parse_search_queries('["alpha", "Alpha", "beta", "gamma"]', "fallback") == [
        "alpha",
        "beta",
        "gamma",
    ]
    sources = dedupe_sources([
        {"results": [
            {"title": "A", "url": "https://example.com/a#section", "content": "one"},
            {"title": "A2", "url": "https://example.com/a", "content": "duplicate"},
            {"title": "Bad", "url": "javascript:alert(1)", "content": "bad"},
        ]},
    ])
    assert sources == [{
        "title": "A",
        "url": "https://example.com/a#section",
        "content": "one",
        "score": 0,
    }]


def test_deep_search_runs_at_most_once_per_turn(monkeypatch):
    calls = 0

    async def fake_search(query, focus, model_id, callback):
        nonlocal calls
        calls += 1
        return {
            "query": query,
            "queries": [query],
            "summary": "evidence [[cite:1]]",
            "results": [{
                "title": "Source",
                "url": "https://example.com",
                "content": "Evidence",
                "score": 0,
            }],
        }

    monkeypatch.setattr(nodes, "deep_search_agent", fake_search)
    tool_call = {
        "id": "call-1",
        "name": "deep_search",
        "args": {"query": "topic"},
        "type": "tool_call",
    }
    first_state = base_state(messages=[AIMessage(content="", tool_calls=[tool_call])])
    first = asyncio.run(nodes.custom_tool_node(first_state, {}))
    assert calls == 1
    assert first["search_iteration"] == 1
    assert first["source_citations"][0]["url"] == "https://example.com"

    second_state = base_state(
        messages=[AIMessage(content="", tool_calls=[tool_call])],
        search_iteration=1,
        source_citations=first["source_citations"],
    )
    second = asyncio.run(nodes.custom_tool_node(second_state, {}))
    assert calls == 1
    assert "最多运行一次" in json.loads(second["messages"][0].content)["error"]


def test_sources_event_is_bound_to_final_answer(monkeypatch):
    class FakeLlm:
        def bind_tools(self, _tools):
            return self

        async def astream(self, _messages):
            yield AIMessageChunk(content="重要结论 [[cite:1]]")

    monkeypatch.setattr(nodes, "create_deepseek_chat", lambda _model: FakeLlm())
    events = []

    async def callback(event):
        events.append(event)

    citations = [{
        "title": "Source",
        "url": "https://example.com",
        "content": "Evidence",
        "score": 0,
    }]
    result = asyncio.run(nodes.chat_node(
        base_state(source_citations=citations),
        {"configurable": {"stream_callback": callback}},
    ))
    answer = result["messages"][0]
    source_event = next(event for event in events if event["type"] == "sources")
    text_event = next(event for event in events if event["type"] == "text_start")
    assert source_event["messageId"] == text_event["messageId"]
    assert answer.additional_kwargs["sources"] == citations
