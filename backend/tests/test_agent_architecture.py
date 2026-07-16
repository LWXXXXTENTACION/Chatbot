import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.agents import general, research, supervisor
from app.cache import CacheLookup
from app.graph import deep_search, model as graph_model, nodes, tool_execution
from app.graph.builder import build_graph
from app.graph.context import AgentRuntimeContext
from app.graph.routing import route_supervisor
from app.tools import GENERAL_AGENT_TOOLS, RESEARCH_AGENT_TOOLS


def base_state(**overrides):
    state = {
        "messages": [HumanMessage(content="test")],
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
    state.update(overrides)
    return state


def runtime(tool_cache=None, search_mode="auto"):
    return SimpleNamespace(
        context=AgentRuntimeContext(
            tool_cache=tool_cache,
            search_mode=search_mode,
        )
    )


def noop_writer(_event):
    return None


async def collect_graph_stream(graph, input_state, *, context):
    events = []
    final_state = None
    async for part in graph.astream(
        input_state,
        context=context,
        stream_mode=["values", "custom"],
        version="v2",
    ):
        if part["type"] == "custom":
            events.append(part["data"])
        elif part["type"] == "values":
            final_state = part["data"]
    return final_state, events


def _assert_acyclic(graph) -> None:
    adjacency: dict[str, list[str]] = {}
    for edge in graph.edges:
        adjacency.setdefault(edge.source, []).append(edge.target)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        assert node not in visiting, f"cycle detected at {node}"
        if node in visited:
            return
        visiting.add(node)
        for target in adjacency.get(node, []):
            visit(target)
        visiting.remove(node)
        visited.add(node)

    visit("__start__")


def test_main_graph_is_a_supervisor_workflow():
    graph = build_graph().get_graph()
    assert set(graph.nodes) == {
        "__start__",
        "prepare_turn",
        "context_manager",
        "supervisor",
        "general_agent",
        "research_agent",
        "supervisor_finalize",
        "__end__",
    }
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert ("prepare_turn", "context_manager") in edges
    assert ("context_manager", "supervisor") in edges
    assert ("supervisor", "general_agent") in edges
    assert ("supervisor", "research_agent") in edges
    assert ("general_agent", "supervisor_finalize") in edges
    assert ("research_agent", "supervisor_finalize") in edges
    assert ("supervisor_finalize", "__end__") in edges
    _assert_acyclic(graph)


def test_general_agent_has_one_bounded_tool_loop():
    graph = general.GENERAL_AGENT_GRAPH.get_graph()
    assert set(graph.nodes) == {
        "__start__", "prepare", "agent", "tools", "tool_limit", "__end__"
    }
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert ("tools", "agent") in edges
    assert general.MAX_TOOL_ROUNDS == 3


def test_deep_search_is_an_inspectable_acyclic_workflow():
    graph = deep_search.DEEP_SEARCH_GRAPH.get_graph()
    assert set(graph.nodes) == {
        "__start__",
        "plan_queries",
        "search_sources",
        "synthesize_brief",
        "__end__",
    }
    _assert_acyclic(graph)


def test_backend_exposes_only_the_authenticated_chat_transport():
    from app.routers.chat import router

    routes = {
        (getattr(route, "path", ""), method)
        for route in router.routes
        for method in getattr(route, "methods", set())
    }
    assert ("/api/chat/stream", "POST") in routes
    assert not any(path in {"/chat/stream", "/ws"} for path, _method in routes)


def test_api_consumes_langgraph_custom_stream_without_runtime_callback():
    from app.routers import chat as chat_router

    human = HumanMessage(content="hello", id="human-1")
    answer = AIMessage(content="world", id="answer-1")

    class FakeGraph:
        async def astream(
            self,
            initial_state,
            *,
            config,
            context,
            stream_mode,
            version,
        ):
            assert initial_state["messages"] == [human]
            assert config["configurable"]["thread_id"] == "conversation"
            assert not hasattr(context, "stream_callback")
            assert stream_mode == ["values", "custom"]
            assert version == "v2"
            yield {
                "type": "custom",
                "ns": (),
                "data": {"type": "text_delta", "messageId": "answer-1", "delta": "world"},
            }
            yield {
                "type": "values",
                "ns": (),
                "data": {"messages": [human, answer], "error": None},
            }

    events = []

    async def send_event(event):
        events.append(event)

    new_messages = asyncio.run(chat_router._run_graph_and_stream(
        FakeGraph(),
        [human],
        "human-1",
        "deepseek-v4-flash",
        "system",
        send_event,
        "user",
        "conversation",
        None,
        "auto",
    ))

    assert new_messages == [answer]
    assert events == [{
        "type": "text_delta",
        "messageId": "answer-1",
        "delta": "world",
    }]


def test_prepare_turn_resets_all_coordination_state():
    assert nodes.prepare_turn_node(base_state(
        supervisor_decision={
            "route": "research_agent",
            "task": "old",
            "reason": "old",
        },
        active_agent="research_agent",
        completed_agents=["research_agent"],
        worker_result="old result",
        source_citations=[{"title": "old", "url": "https://old", "content": "old"}],
        error="old error",
    )) == {
        "supervisor_decision": None,
        "active_agent": None,
        "completed_agents": [],
        "worker_result": "",
        "source_citations": [],
        "context_report": None,
        "error": None,
    }


@pytest.mark.parametrize("search_mode", ["web", "deep"])
def test_explicit_search_mode_is_assigned_to_research_without_router_llm(
    monkeypatch,
    search_mode,
):
    monkeypatch.setattr(
        supervisor,
        "create_deepseek_chat",
        lambda *_args, **_kwargs: pytest.fail("explicit search must route deterministically"),
    )
    result = asyncio.run(supervisor.supervisor_node(
        base_state(messages=[HumanMessage(content="research this")]),
        runtime(search_mode=search_mode),
        noop_writer,
    ))
    assert result["supervisor_decision"]["route"] == "research_agent"
    assert search_mode in result["supervisor_decision"]["reason"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"route":"general_agent","task":"calculate","reason":"math"}', "general_agent"),
        ('{"route":"research_agent","task":"latest news","reason":"current"}', "research_agent"),
    ],
)
def test_supervisor_model_returns_an_auditable_assignment(monkeypatch, raw, expected):
    class FakeSupervisor:
        async def ainvoke(self, _messages):
            return AIMessage(content=raw)

    monkeypatch.setattr(
        supervisor,
        "create_deepseek_chat",
        lambda *_args, **_kwargs: FakeSupervisor(),
    )
    result = asyncio.run(supervisor.supervisor_node(
        base_state(), runtime(), noop_writer
    ))
    decision = result["supervisor_decision"]
    assert decision["route"] == expected
    assert decision["task"]
    assert decision["reason"]


def test_supervisor_has_a_deterministic_parse_fallback():
    research_decision = supervisor._parse_decision("not json", "帮我搜索最新新闻")
    general_decision = supervisor._parse_decision("not json", "计算 1+1")
    assert research_decision["route"] == "research_agent"
    assert general_decision["route"] == "general_agent"


def test_supervisor_router_uses_only_the_declared_assignment():
    assert route_supervisor(base_state(supervisor_decision={
        "route": "research_agent",
        "task": "topic",
        "reason": "needs sources",
    })) == "research_agent"
    assert route_supervisor(base_state(supervisor_decision={
        "route": "general_agent",
        "task": "write",
        "reason": "no research",
    })) == "general_agent"


def test_tool_ownership_is_separated_between_workers():
    assert [tool.name for tool in GENERAL_AGENT_TOOLS] == [
        "get_weather", "calculate", "create_artifact"
    ]
    assert [tool.name for tool in RESEARCH_AGENT_TOOLS] == ["web_search", "deep_search"]


def test_general_agent_direct_result_is_internal_until_supervisor_finalizes(monkeypatch):
    class FakeGeneral:
        def bind_tools(self, _tools):
            return self

        async def astream(self, _messages):
            yield AIMessageChunk(content="worker result")

    monkeypatch.setattr(graph_model, "create_deepseek_chat", lambda _model: FakeGeneral())
    events = []

    result, trace = asyncio.run(general.run_general_agent(
        task="write",
        conversation_messages=[HumanMessage(content="write")],
        model_id="deepseek-v4-flash",
        system_prompt="",
        context=runtime().context,
        writer=events.append,
    ))
    assert result == "worker result"
    assert trace == []
    assert not any(event["type"].startswith("text_") for event in events)


def test_general_agent_can_call_tools_and_persist_the_trace(monkeypatch):
    class ToolDecision:
        def bind_tools(self, tools):
            assert [tool.name for tool in tools] == [
                "get_weather", "calculate", "create_artifact"
            ]
            return self

        async def astream(self, _messages):
            yield AIMessageChunk(content="", tool_call_chunks=[{
                "id": "calc-call",
                "name": "calculate",
                "args": '{"expression":"1+1"}',
                "index": 0,
            }])

    class ToolAnswer:
        def bind_tools(self, _tools):
            return self

        async def astream(self, _messages):
            yield AIMessageChunk(content="calculation result is 2")

    models = iter([ToolDecision(), ToolAnswer()])
    monkeypatch.setattr(graph_model, "create_deepseek_chat", lambda _model: next(models))
    result, trace = asyncio.run(general.run_general_agent(
        task="calculate 1+1",
        conversation_messages=[HumanMessage(content="calculate 1+1")],
        model_id="deepseek-v4-flash",
        system_prompt="",
        context=runtime().context,
        writer=noop_writer,
    ))
    assert result == "calculation result is 2"
    assert [type(message).__name__ for message in trace] == ["AIMessage", "ToolMessage"]
    assert trace[0].tool_calls[0]["name"] == "calculate"
    assert json.loads(trace[1].content)["result"] == 2


def test_general_agent_rejects_search_tools():
    messages = asyncio.run(tool_execution.execute_general_tool_batch(
        [{
            "id": "search-call",
            "name": "deep_search",
            "args": {"query": "topic"},
            "type": "tool_call",
        }],
        model_id="deepseek-v4-flash",
        context=runtime().context,
        writer=noop_writer,
    ))
    assert messages[0].status == "error"
    assert "只允许一个搜索任务" in json.loads(messages[0].content)["error"]


def test_general_agent_tool_loop_stops_with_only_complete_protocol_pairs(monkeypatch):
    class RepeatingToolCall:
        def __init__(self, index):
            self.index = index

        def bind_tools(self, _tools):
            return self

        async def astream(self, _messages):
            yield AIMessageChunk(content="", tool_call_chunks=[{
                "id": f"calc-{self.index}",
                "name": "calculate",
                "args": '{"expression":"1+1"}',
                "index": 0,
            }])

    models = iter(RepeatingToolCall(index) for index in range(4))
    monkeypatch.setattr(graph_model, "create_deepseek_chat", lambda _model: next(models))
    result, trace = asyncio.run(general.run_general_agent(
        task="keep calculating",
        conversation_messages=[HumanMessage(content="calculate")],
        model_id="deepseek-v4-flash",
        system_prompt="",
        context=runtime().context,
        writer=noop_writer,
    ))
    assert "3 轮工具调用上限" in result
    assert len(trace) == 6
    assert [type(message).__name__ for message in trace] == [
        "AIMessage",
        "ToolMessage",
        "AIMessage",
        "ToolMessage",
        "AIMessage",
        "ToolMessage",
    ]


@pytest.mark.parametrize(
    ("search_mode", "expected_tool"),
    [("web", "web_search"), ("deep", "deep_search"), ("auto", "deep_search")],
)
def test_research_agent_owns_fast_and_deep_search(monkeypatch, search_mode, expected_tool):
    async def fake_execute(state, _context, _writer):
        call = state["messages"][-1].tool_calls[0]
        assert call["name"] == expected_tool
        output = {
            "summary": "research brief [[cite:1]]",
            "results": [{
                "title": "Source",
                "url": "https://example.com",
                "content": "Evidence",
            }],
        }
        return {
            "messages": [ToolMessage(
                content=json.dumps(output),
                tool_call_id=call["id"],
                name=call["name"],
            )],
            "source_citations": output["results"],
        }

    monkeypatch.setattr(research, "execute_tool_batch", fake_execute)
    result = asyncio.run(research.research_worker_node(
        base_state(supervisor_decision={
            "route": "research_agent",
            "task": "topic",
            "reason": "needs research",
        }),
        runtime(search_mode=search_mode),
        noop_writer,
    ))
    assert result["worker_result"] == "research brief [[cite:1]]"
    assert [type(message).__name__ for message in result["messages"]] == [
        "AIMessage", "ToolMessage"
    ]
    assert result["completed_agents"] == ["research_agent"]


def test_supervisor_finalization_streams_one_user_facing_answer(monkeypatch):
    class FakeFinal:
        def bind_tools(self, _tools):
            raise AssertionError("Supervisor finalization cannot bind tools")

        async def astream(self, messages):
            self.messages = messages
            yield AIMessageChunk(content="integrated answer [[cite:1]]")

    llm = FakeFinal()
    monkeypatch.setattr(graph_model, "create_deepseek_chat", lambda _model: llm)
    events = []

    citations = [{
        "title": "Source",
        "url": "https://example.com",
        "content": "Evidence",
    }]
    result = asyncio.run(supervisor.supervisor_finalize_node(
        base_state(
            supervisor_decision={
                "route": "research_agent",
                "task": "topic",
                "reason": "research",
            },
            completed_agents=["research_agent"],
            worker_result="brief",
            source_citations=citations,
        ),
        events.append,
    ))
    assert result["messages"][0].content == "integrated answer [[cite:1]]"
    assert result["completed_agents"] == ["research_agent", "supervisor"]
    assert any(
        isinstance(message, SystemMessage) and "Worker 结果：\nbrief" in str(message.content)
        for message in llm.messages
    )
    assert [event["type"] for event in events if event["type"] == "sources"] == ["sources"]


def test_full_supervisor_general_path(monkeypatch):
    class FakeSupervisor:
        async def ainvoke(self, _messages):
            return AIMessage(content=json.dumps({
                "route": "general_agent",
                "task": "calculate 1+1",
                "reason": "math task",
            }))

    class GeneralWorker:
        def bind_tools(self, _tools):
            return self

        async def astream(self, _messages):
            yield AIMessageChunk(content="worker says 2")

    class SupervisorFinal:
        async def astream(self, _messages):
            yield AIMessageChunk(content="1+1 等于 2。")

    monkeypatch.setattr(
        supervisor,
        "create_deepseek_chat",
        lambda *_args, **_kwargs: FakeSupervisor(),
    )
    models = iter([GeneralWorker(), SupervisorFinal()])
    monkeypatch.setattr(graph_model, "create_deepseek_chat", lambda _model: next(models))
    result, events = asyncio.run(collect_graph_stream(
        build_graph(),
        {
            "messages": [HumanMessage(content="1+1?")],
            "model_id": "deepseek-v4-flash",
            "system_prompt": "",
            "user_id": "user",
            "conversation_id": "conversation",
        },
        context=runtime().context,
    ))
    assert not hasattr(runtime().context, "stream_callback")
    assert result["error"] is None
    assert [type(message).__name__ for message in result["messages"]] == [
        "HumanMessage", "AIMessage"
    ]
    assert result["messages"][-1].content == "1+1 等于 2。"
    assert [event["type"] for event in events if event["type"].startswith("text_")] == [
        "text_start", "text_delta", "text_end"
    ]


def test_full_supervisor_research_path_persists_search_trace(monkeypatch):
    async def fake_deep_search(**_kwargs):
        return {
            "query": "topic",
            "queries": ["topic"],
            "summary": "brief [[cite:1]]",
            "results": [{
                "title": "Source",
                "url": "https://example.com",
                "content": "Evidence",
            }],
        }

    class SupervisorFinal:
        async def astream(self, _messages):
            yield AIMessageChunk(content="research answer [[cite:1]]")

    monkeypatch.setattr(tool_execution, "run_deep_search_workflow", fake_deep_search)
    monkeypatch.setattr(graph_model, "create_deepseek_chat", lambda _model: SupervisorFinal())
    result = asyncio.run(build_graph().ainvoke(
        {
            "messages": [HumanMessage(content="research topic")],
            "model_id": "deepseek-v4-flash",
            "system_prompt": "",
            "user_id": "user",
            "conversation_id": "conversation",
        },
        context=runtime(search_mode="deep").context,
    ))
    assert result["error"] is None
    assert [type(message).__name__ for message in result["messages"]] == [
        "HumanMessage", "AIMessage", "ToolMessage", "AIMessage"
    ]
    assert result["messages"][1].tool_calls[0]["name"] == "deep_search"
    assert result["messages"][-1].additional_kwargs["sources"][0]["title"] == "Source"


def test_deep_search_cache_hit_skips_research_graph(monkeypatch):
    async def should_not_run(**_kwargs):
        raise AssertionError("deep-search graph should be skipped on cache hit")

    class FakeCache:
        async def get(self, name, args, model_id=""):
            return CacheLookup(True, {
                "summary": "cached [[cite:1]]",
                "results": [{
                    "title": "Cached",
                    "url": "https://example.com",
                    "content": "evidence",
                }],
            })

        async def put(self, *_args, **_kwargs):
            raise AssertionError("cache hit must not write")

    monkeypatch.setattr(tool_execution, "run_deep_search_workflow", should_not_run)
    result = asyncio.run(tool_execution.execute_tool_batch(
        base_state(messages=[AIMessage(content="", tool_calls=[{
            "id": "cached-call",
            "name": "deep_search",
            "args": {"query": "cached topic"},
            "type": "tool_call",
        }])]),
        runtime(tool_cache=FakeCache()).context,
        noop_writer,
    ))
    assert result["source_citations"][0]["title"] == "Cached"


def test_deep_search_query_and_source_bounds():
    assert deep_search.parse_search_queries(
        '["alpha", "Alpha", "beta", "gamma"]',
        "fallback",
    ) == ["alpha", "beta", "gamma"]
    assert deep_search.dedupe_sources([{"results": [
        {"title": "A", "url": "https://example.com/a#section", "content": "one"},
        {"title": "A2", "url": "https://example.com/a", "content": "duplicate"},
        {"title": "Bad", "url": "javascript:alert(1)", "content": "bad"},
    ]}])[0]["title"] == "A"


def test_deep_search_subgraph_forwards_custom_events(monkeypatch):
    class FakeDeepSearchGraph:
        async def astream(self, _input, *, stream_mode, version):
            assert stream_mode == ["values", "custom"]
            assert version == "v2"
            yield {
                "type": "custom",
                "ns": (),
                "data": {
                    "type": "activity",
                    "kind": "searching",
                    "message": "searching",
                },
            }
            yield {
                "type": "values",
                "ns": (),
                "data": {
                    "query": "topic",
                    "queries": ["topic"],
                    "summary": "brief",
                    "results": [],
                },
            }

    monkeypatch.setattr(deep_search, "DEEP_SEARCH_GRAPH", FakeDeepSearchGraph())
    events = []
    result = asyncio.run(deep_search.run_deep_search_workflow(
        query="topic",
        focus="",
        model_id="deepseek-v4-flash",
        writer=events.append,
    ))

    assert result["summary"] == "brief"
    assert events[0]["type"] == "activity"


def test_supervisor_workflow_resumes_messages_by_thread_id(monkeypatch):
    class FakeSupervisor:
        async def ainvoke(self, _messages):
            return AIMessage(content=(
                '{"route":"general_agent","task":"answer",'
                '"reason":"general"}'
            ))

    class Worker:
        def bind_tools(self, _tools):
            return self

        async def astream(self, messages):
            count = sum(isinstance(message, HumanMessage) for message in messages)
            yield AIMessageChunk(content=f"worker-{count}")

    class Final:
        async def astream(self, messages):
            count = sum(isinstance(message, HumanMessage) for message in messages)
            yield AIMessageChunk(content=f"final-{count}")

    monkeypatch.setattr(
        supervisor,
        "create_deepseek_chat",
        lambda *_args, **_kwargs: FakeSupervisor(),
    )
    model_calls = iter([Worker(), Final(), Worker(), Final()])
    monkeypatch.setattr(graph_model, "create_deepseek_chat", lambda _model: next(model_calls))
    graph = build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "conversation-1"}}
    common = {
        "model_id": "deepseek-v4-flash",
        "system_prompt": "",
        "user_id": "user",
        "conversation_id": "conversation-1",
    }

    async def run():
        first = await graph.ainvoke(
            {**common, "messages": [HumanMessage(content="first")]},
            config=config,
            context=runtime().context,
        )
        second = await graph.ainvoke(
            {**common, "messages": [HumanMessage(content="second")]},
            config=config,
            context=runtime().context,
        )
        snapshot = await graph.aget_state(config)
        return first, second, snapshot

    first, second, snapshot = asyncio.run(run())
    assert len(first["messages"]) == 2
    assert len(second["messages"]) == 4
    assert second["messages"][-1].content == "final-2"
    assert snapshot.values["completed_agents"] == ["general_agent", "supervisor"]
    assert snapshot.values["source_citations"] == []
