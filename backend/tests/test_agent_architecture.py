import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from app.graph import nodes
from app.graph.builder import build_graph
from app.graph.context import AgentRuntimeContext
from app.graph.deep_search import dedupe_sources, parse_search_queries
from app.cache import CacheLookup
from app.tools import (
    DEEP_SEARCH_MODE_TOOLS,
    DEEP_SEARCH_TOOLS,
    FAST_SEARCH_TOOLS,
    MAIN_AGENT_TOOLS,
)


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


def runtime(callback=None, tool_cache=None, search_mode="auto"):
    return SimpleNamespace(
        context=AgentRuntimeContext(
            stream_callback=callback,
            tool_cache=tool_cache,
            search_mode=search_mode,
        )
    )


def test_graph_has_one_main_agent_and_one_tool_node():
    graph = build_graph().get_graph()
    assert set(graph.nodes) == {"__start__", "main_agent", "tools", "__end__"}


def test_main_agent_requires_sentence_level_clickable_citations():
    prompt = nodes.MAIN_AGENT_SYSTEM_PROMPT
    assert "每个依赖来源的事实句" in prompt
    assert "句末标点前" in prompt
    assert "[[cite:1]]" in prompt
    assert "前端会把引用编号转换成可点击来源链接" in prompt


def test_main_agent_cannot_call_raw_web_search():
    assert [tool.name for tool in MAIN_AGENT_TOOLS] == [
        "get_weather",
        "calculate",
        "create_artifact",
        "deep_search",
    ]
    assert [tool.name for tool in DEEP_SEARCH_TOOLS] == ["web_search"]
    assert [tool.name for tool in FAST_SEARCH_TOOLS] == [
        "get_weather",
        "calculate",
        "create_artifact",
        "web_search",
    ]
    assert [tool.name for tool in DEEP_SEARCH_MODE_TOOLS] == [
        "get_weather",
        "calculate",
        "create_artifact",
        "deep_search",
    ]


@pytest.mark.parametrize(
    ("search_mode", "expected_tool"),
    [("web", "web_search"), ("deep", "deep_search")],
)
def test_explicit_search_mode_creates_the_selected_tool_call_without_llm(
    monkeypatch,
    search_mode,
    expected_tool,
):
    monkeypatch.setattr(
        nodes,
        "create_deepseek_chat",
        lambda _model: pytest.fail("explicit search must not call the LLM first"),
    )
    events = []

    async def callback(event):
        events.append(event)

    result = asyncio.run(nodes.chat_node(
        base_state(messages=[HumanMessage(content="current question")]),
        runtime(callback, search_mode=search_mode),
    ))

    message = result["messages"][0]
    assert message.tool_calls[0]["name"] == expected_tool
    assert message.tool_calls[0]["args"]["query"] == "current question"
    assert message.tool_calls[0]["id"].startswith(nodes.FORCED_SEARCH_CALL_PREFIX)
    assert [event["type"] for event in events] == [
        "tool_call_start",
        "tool_call_delta",
        "tool_call_end",
    ]


def test_explicit_search_followup_never_sends_tool_choice(monkeypatch):
    class FakeLlm:
        def __init__(self):
            self.bind_kwargs = None
            self.tools = []
            self.messages = []

        def bind_tools(self, tools, **kwargs):
            self.tools = tools
            self.bind_kwargs = kwargs
            return self

        async def astream(self, messages):
            self.messages = messages
            yield AIMessageChunk(content="final answer")

    llm = FakeLlm()
    monkeypatch.setattr(nodes, "create_deepseek_chat", lambda _model: llm)

    call_id = f"{nodes.FORCED_SEARCH_CALL_PREFIX}test"
    asyncio.run(nodes.chat_node(
        base_state(
            search_iteration=1,
            messages=[
                HumanMessage(content="btc"),
                AIMessage(
                    content="",
                    tool_calls=[{
                        "id": call_id,
                        "name": "web_search",
                        "args": {"query": "btc"},
                        "type": "tool_call",
                    }],
                ),
                ToolMessage(
                    content=json.dumps({
                        "query": "btc",
                        "results": [{
                            "title": "Bitcoin",
                            "url": "https://example.com/btc",
                            "content": "evidence",
                        }],
                    }),
                    tool_call_id=call_id,
                    name="web_search",
                ),
            ],
        ),
        runtime(search_mode="web"),
    ))

    assert llm.bind_kwargs == {}
    assert [tool.name for tool in llm.tools] == [
        "get_weather",
        "calculate",
        "create_artifact",
    ]
    assert not any(
        isinstance(message, ToolMessage)
        or isinstance(message, AIMessage) and message.tool_calls
        for message in llm.messages
    )
    evidence_message = next(
        message
        for message in llm.messages
        if isinstance(message, SystemMessage) and "本回合搜索证据" in str(message.content)
    )
    assert '"query": "btc"' in str(evidence_message.content)


def test_fast_web_search_emits_sanitized_sources(monkeypatch):
    class FakeWebSearch:
        async def ainvoke(self, args):
            return {
                "query": args["query"],
                "results": [
                    {
                        "title": "Primary",
                        "url": "https://example.com/source#details",
                        "content": "Evidence",
                    },
                    {
                        "title": "Unsafe",
                        "url": "javascript:alert(1)",
                        "content": "Ignore",
                    },
                ],
            }

    monkeypatch.setitem(nodes.TOOL_MAP, "web_search", FakeWebSearch())
    call = {
        "id": "web-call",
        "name": "web_search",
        "args": {"query": "latest topic"},
        "type": "tool_call",
    }
    events = []

    async def callback(event):
        events.append(event)

    result = asyncio.run(nodes.custom_tool_node(
        base_state(messages=[AIMessage(content="", tool_calls=[call])]),
        runtime(callback, search_mode="web"),
    ))

    assert result["search_iteration"] == 1
    assert result["source_citations"] == [{
        "title": "Primary",
        "url": "https://example.com/source#details",
        "content": "Evidence",
        "score": 0,
    }]
    assert next(event for event in events if event["type"] == "tool_result")["cached"] is False


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
    first = asyncio.run(nodes.custom_tool_node(first_state, runtime()))
    assert calls == 1
    assert first["search_iteration"] == 1
    assert first["source_citations"][0]["url"] == "https://example.com"

    second_state = base_state(
        messages=[AIMessage(content="", tool_calls=[tool_call])],
        search_iteration=1,
        source_citations=first["source_citations"],
    )
    second = asyncio.run(nodes.custom_tool_node(second_state, runtime()))
    assert calls == 1
    assert "最多运行一次" in json.loads(second["messages"][0].content)["error"]


def test_deep_search_cache_hit_skips_delegate(monkeypatch):
    async def should_not_run(**_kwargs):
        raise AssertionError("deep-search delegate should be skipped on a cache hit")

    class FakeCache:
        async def get(self, name, args, model_id=""):
            assert name == "deep_search"
            assert model_id == "deepseek-v4-flash"
            return CacheLookup(True, {
                "queries": [args["query"]],
                "summary": "cached [[cite:1]]",
                "results": [{"title": "Cached", "url": "https://example.com", "content": "evidence"}],
            })

        async def put(self, *_args, **_kwargs):
            raise AssertionError("cache hit must not write")

    monkeypatch.setattr(nodes, "deep_search_agent", should_not_run)
    tool_call = {
        "id": "cached-call",
        "name": "deep_search",
        "args": {"query": "cached topic"},
        "type": "tool_call",
    }
    events = []

    async def callback(event):
        events.append(event)

    result = asyncio.run(nodes.custom_tool_node(
        base_state(messages=[AIMessage(content="", tool_calls=[tool_call])]),
        runtime(callback, FakeCache()),
    ))
    assert result["source_citations"][0]["title"] == "Cached"
    assert next(event for event in events if event["type"] == "tool_result")["cached"] is True


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
        runtime(callback),
    ))
    answer = result["messages"][0]
    source_event = next(event for event in events if event["type"] == "sources")
    text_event = next(event for event in events if event["type"] == "text_start")
    assert source_event["messageId"] == text_event["messageId"]
    assert answer.additional_kwargs["sources"] == citations
