import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver

from app.agents import artifact, general, research, supervisor
from app.cache import CacheLookup
from app.graph import deep_search, model as graph_model, nodes, tool_execution
from app.graph.builder import build_graph
from app.graph.context import AgentRuntimeContext
from app.graph.routing import route_supervisor
from app.tools import GENERAL_AGENT_TOOLS, RESEARCH_AGENT_TOOLS
from app.tools.registry import (
    MAX_ARTIFACT_CONTENT_CHARS,
    MAX_BATCH_TOOL_CALLS,
    MAX_CONCURRENT_TOOLS,
    MAX_TURN_TOOL_CALLS,
    TOOL_REGISTRY,
)


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
        "retrieved_context": [],
        "context_archive_queue": [],
        "general_task_route": None,
        "tool_rounds": 0,
        "artifact_plan": None,
        "artifact_content": "",
        "research_plan": None,
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
        subgraphs=True,
        version="v2",
    ):
        if part["type"] == "custom":
            events.append(part["data"])
        elif part["type"] == "values" and not part.get("ns"):
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
        "retrieve_context",
        "context_manager",
        "archive_context",
        "supervisor",
        "general_agent",
        "research_agent",
        "supervisor_finalize",
        "__end__",
    }
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert ("prepare_turn", "retrieve_context") in edges
    assert ("retrieve_context", "context_manager") in edges
    assert ("context_manager", "archive_context") in edges
    assert ("archive_context", "supervisor") in edges
    assert ("supervisor", "general_agent") in edges
    assert ("supervisor", "research_agent") in edges
    assert ("general_agent", "supervisor_finalize") in edges
    assert ("research_agent", "supervisor_finalize") in edges
    assert ("supervisor_finalize", "__end__") in edges
    _assert_acyclic(graph)


def test_main_graph_xray_expands_compiled_worker_subgraphs():
    nodes = set(build_graph().get_graph(xray=True).nodes)
    assert "general_agent:prepare_general" in nodes
    assert "general_agent:generate_artifact" in nodes
    assert "general_agent:artifact_tools" in nodes
    assert "research_agent:prepare_research" in nodes
    assert "research_agent:research_tools" in nodes


def test_general_agent_exposes_standard_and_artifact_workflows():
    graph = general.GENERAL_AGENT_GRAPH.get_graph()
    assert set(graph.nodes) == {
        "__start__",
        "prepare_general",
        "general_model",
        "general_tools",
        "general_tool_limit",
        "prepare_artifact",
        "generate_artifact",
        "build_artifact_call",
        "artifact_tools",
        "finalize_artifact",
        "complete_general",
        "__end__",
    }
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert ("prepare_general", "general_model") in edges
    assert ("prepare_general", "prepare_artifact") in edges
    assert ("general_tools", "general_model") in edges
    assert ("prepare_artifact", "generate_artifact") in edges
    assert ("build_artifact_call", "artifact_tools") in edges
    assert ("artifact_tools", "finalize_artifact") in edges
    assert general.MAX_TOOL_ROUNDS == 3


def test_research_agent_is_an_explicit_four_node_workflow():
    graph = research.RESEARCH_AGENT_GRAPH.get_graph()
    assert set(graph.nodes) == {
        "__start__",
        "prepare_research",
        "build_research_call",
        "research_tools",
        "finalize_research",
        "__end__",
    }
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert ("prepare_research", "build_research_call") in edges
    assert ("build_research_call", "research_tools") in edges
    assert ("research_tools", "finalize_research") in edges


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
    assert ("/api/chat/stream/{stream_id}", "DELETE") in routes
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
            subgraphs,
            version,
        ):
            assert initial_state["messages"] == [human]
            assert config["configurable"]["thread_id"] == "conversation"
            assert not hasattr(context, "stream_callback")
            assert stream_mode == ["values", "custom"]
            assert subgraphs is True
            assert version == "v2"
            yield {
                "type": "custom",
                "ns": ("general_agent:child",),
                "data": {"type": "text_delta", "messageId": "answer-1", "delta": "world"},
            }
            yield {
                "type": "values",
                "ns": ("general_agent:child",),
                "data": {"messages": [human], "error": "nested state is not final"},
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
            None,
            "auto",
            "stream-test",
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
        "retrieved_context": [],
        "context_archive_queue": [],
        "general_task_route": None,
        "tool_rounds": 0,
        "artifact_plan": None,
        "artifact_content": "",
        "research_plan": None,
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
        base_state(
            model_id="deepseek-reasoner",
            messages=[HumanMessage(content="research this")],
        ),
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


def test_artifact_prompt_requires_a4_html_for_pdf_preview():
    assert "PDF 预览" in artifact.ARTIFACT_GENERATION_PROMPT
    assert "@page" in artifact.ARTIFACT_GENERATION_PROMPT
    assert "完整正文" in artifact.ARTIFACT_GENERATION_PROMPT


def test_reasoner_artifact_workflow_wraps_document_without_forced_tool_choice(
    monkeypatch,
):
    captured = {}

    async def fake_stream_model_message(state, **kwargs):
        captured["model_id"] = state["model_id"]
        captured["tools"] = kwargs["tools"]
        captured["strip_tool_protocol"] = kwargs["strip_tool_protocol"]
        captured["has_tool_choice"] = "tool_choice" in kwargs
        return AIMessage(content=(
            "```html\n<!DOCTYPE html><html><body><h1>PDF 预览</h1></body></html>\n```"
        ))

    monkeypatch.setattr(artifact, "stream_model_message", fake_stream_model_message)
    result, events = asyncio.run(collect_graph_stream(
        general.GENERAL_AGENT_GRAPH,
        base_state(
            model_id="deepseek-reasoner",
            messages=[HumanMessage(content="生成 PDF")],
            supervisor_decision={
                "route": "general_agent",
                "task": "生成 PDF",
                "reason": "artifact",
            },
        ),
        context=runtime().context,
    ))

    assert captured == {
        "model_id": "deepseek-reasoner",
        "tools": None,
        "strip_tool_protocol": True,
        "has_tool_choice": False,
    }
    assert result["worker_result"] == "文档工件已创建并已在侧边栏打开。"
    message = result["messages"][-2]
    assert message.tool_calls[0]["name"] == "create_artifact"
    assert message.tool_calls[0]["args"]["kind"] == "html"
    assert message.tool_calls[0]["args"]["content"].startswith("<!DOCTYPE html>")
    tool_events = [event for event in events if event["type"].startswith("tool_call_")]
    assert [event["type"] for event in tool_events] == [
        "tool_call_start",
        "tool_call_delta",
        "tool_call_end",
    ]
    assert json.loads(tool_events[1]["delta"]) == message.tool_calls[0]["args"]


def test_artifact_finalize_node_uses_the_tool_result_without_another_model_call():
    result = artifact.finalize_artifact_node(base_state(
        messages=[ToolMessage(
            content='{"ok":true}',
            tool_call_id="artifact-call",
            name="create_artifact",
        )],
    ))
    assert result["worker_result"] == "文档工件已创建并已在侧边栏打开。"


def test_reasoner_artifact_workflow_executes_once_and_persists_trace(monkeypatch):
    calls = 0

    async def fake_stream_model_message(_state, **_kwargs):
        nonlocal calls
        calls += 1
        return AIMessage(content=(
            "<!DOCTYPE html><html><body><article>稳定文档</article></body></html>"
        ))

    monkeypatch.setattr(artifact, "stream_model_message", fake_stream_model_message)
    result, events = asyncio.run(collect_graph_stream(
        general.GENERAL_AGENT_GRAPH,
        base_state(
            model_id="deepseek-reasoner",
            messages=[HumanMessage(content="生成 PDF")],
            supervisor_decision={
                "route": "general_agent",
                "task": "生成 PDF",
                "reason": "artifact",
            },
        ),
        context=runtime().context,
    ))

    assert calls == 1
    assert result["worker_result"] == "文档工件已创建并已在侧边栏打开。"
    trace = result["messages"][1:]
    assert [type(message).__name__ for message in trace] == ["AIMessage", "ToolMessage"]
    assert trace[0].tool_calls[0]["name"] == "create_artifact"
    assert json.loads(trace[1].content)["ok"] is True
    assert [event["type"] for event in events if event["type"].startswith("tool_")] == [
        "tool_call_start",
        "tool_call_delta",
        "tool_call_end",
        "tool_result",
    ]


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        ("生成pdf", True),
        ("创建一个 HTML 页面", True),
        ("写一篇介绍，直接在聊天回复，不要创建文档", False),
    ],
)
def test_artifact_intent_detection_respects_explicit_chat_only_request(task, expected):
    assert artifact.artifact_required(task) is expected


def test_general_agent_direct_result_is_internal_until_supervisor_finalizes(monkeypatch):
    class FakeGeneral:
        def bind_tools(self, _tools):
            return self

        async def astream(self, _messages):
            yield AIMessageChunk(content="worker result")

    monkeypatch.setattr(graph_model, "create_deepseek_chat", lambda _model: FakeGeneral())
    events = []

    result, events = asyncio.run(collect_graph_stream(
        general.GENERAL_AGENT_GRAPH,
        base_state(
            messages=[HumanMessage(content="write")],
            supervisor_decision={
                "route": "general_agent",
                "task": "write",
                "reason": "general",
            },
        ),
        context=runtime().context,
    ))
    assert result["worker_result"] == "worker result"
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], HumanMessage)
    assert result["messages"][0].content == "write"
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
    result = asyncio.run(general.GENERAL_AGENT_GRAPH.ainvoke(
        base_state(
            messages=[HumanMessage(content="calculate 1+1")],
            supervisor_decision={
                "route": "general_agent",
                "task": "calculate 1+1",
                "reason": "math",
            },
        ),
        context=runtime().context,
    ))
    assert result["worker_result"] == "calculation result is 2"
    trace = result["messages"][1:]
    assert [type(message).__name__ for message in trace] == ["AIMessage", "ToolMessage"]
    assert trace[0].tool_calls[0]["name"] == "calculate"
    assert json.loads(trace[1].content)["result"] == 2


def test_stream_model_message_keeps_late_tool_id_and_replays_early_args(monkeypatch):
    class LateIdArtifactModel:
        def bind_tools(self, _tools):
            return self

        async def astream(self, _messages):
            yield AIMessageChunk(content="", tool_call_chunks=[{
                "name": "create_artifact",
                "args": '{"title":"演示","kind":"html",',
                "index": 0,
            }])
            yield AIMessageChunk(content="", tool_call_chunks=[{
                "id": "artifact-real-id",
                "name": None,
                "args": '"content":"<h1>你好</h1>"}',
                "index": 0,
            }])

    monkeypatch.setattr(
        graph_model,
        "create_deepseek_chat",
        lambda _model: LateIdArtifactModel(),
    )
    events = []
    message = asyncio.run(graph_model.stream_model_message(
        base_state(),
        writer=events.append,
        system_prompts=[],
        tools=GENERAL_AGENT_TOOLS,
        attach_sources=False,
        emit_text=False,
        emit_reasoning=False,
    ))

    tool_events = [event for event in events if event["type"].startswith("tool_call_")]
    assert [event["type"] for event in tool_events] == [
        "tool_call_start",
        "tool_call_delta",
        "tool_call_end",
    ]
    assert {event["toolCallId"] for event in tool_events} == {"artifact-real-id"}
    assert json.loads(tool_events[1]["delta"])["content"] == "<h1>你好</h1>"
    assert message.tool_calls[0]["id"] == "artifact-real-id"
    assert message.tool_calls[0]["args"]["title"] == "演示"


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
    assert json.loads(messages[0].content)["code"] == "tool_not_allowed"


def test_tool_policy_limits_batch_concurrency_and_output(monkeypatch):
    tracker = {"active": 0, "peak": 0, "calls": 0}

    async def large_calculation(expression: str):
        tracker["active"] += 1
        tracker["calls"] += 1
        tracker["peak"] = max(tracker["peak"], tracker["active"])
        await asyncio.sleep(0.02)
        tracker["active"] -= 1
        return {"expression": expression, "payload": "x" * 20_000}

    original = TOOL_REGISTRY.get("calculate", "general_agent")
    assert original is not None
    fake_tool = StructuredTool.from_function(
        coroutine=large_calculation,
        name="calculate",
        description="bounded test calculator",
    )
    monkeypatch.setitem(
        TOOL_REGISTRY._policies,
        "calculate",
        replace(
            original,
            tool=fake_tool,
            max_model_output_chars=1_000,
            max_display_output_chars=2_000,
        ),
    )
    events = []
    calls = [{
        "id": f"calc-{index}",
        "name": "calculate",
        "args": {"expression": "1+1"},
        "type": "tool_call",
    } for index in range(8)]

    messages = asyncio.run(tool_execution.execute_general_tool_batch(
        calls,
        model_id="deepseek-v4-flash",
        context=runtime().context,
        writer=events.append,
    ))

    assert MAX_BATCH_TOOL_CALLS == MAX_CONCURRENT_TOOLS == 3
    assert tracker == {"active": 0, "peak": 3, "calls": 3}
    assert len(messages) == 8
    assert all(len(str(message.content)) <= 1_000 for message in messages[:3])
    assert [
        json.loads(message.content).get("code") for message in messages[3:]
    ] == ["batch_call_limit"] * 5
    completed_events = [event for event in events if event["status"] == "success"]
    assert all(event["outputTruncated"] for event in completed_events)
    assert all(
        len(json.dumps(event["result"], ensure_ascii=False, separators=(",", ":")))
        <= 2_000
        for event in completed_events
    )


def test_tool_policy_limits_total_calls_across_batches():
    context = runtime().context
    call_index = 0

    async def execute(count):
        nonlocal call_index
        calls = []
        for _ in range(count):
            calls.append({
                "id": f"calc-{call_index}",
                "name": "calculate",
                "args": {"expression": "1+1"},
                "type": "tool_call",
            })
            call_index += 1
        return await tool_execution.execute_general_tool_batch(
            calls,
            model_id="deepseek-v4-flash",
            context=context,
            writer=noop_writer,
        )

    first, second, third = asyncio.run(_three_batches(execute))
    assert all(message.status == "success" for message in [*first, *second])
    assert json.loads(third[0].content)["code"] == "turn_call_limit"
    assert context.tool_budget.total_calls == MAX_TURN_TOOL_CALLS == 6


async def _three_batches(execute):
    return await execute(3), await execute(3), await execute(1)


def test_tool_policy_limits_deep_search_and_artifact_per_turn(monkeypatch):
    searches = 0

    async def fake_deep_search(**kwargs):
        nonlocal searches
        searches += 1
        return {
            "summary": "ok",
            "query": kwargs["query"],
            "results": [{
                "title": "Source",
                "url": "https://example.com",
                "content": "Evidence",
            }],
        }

    monkeypatch.setattr(tool_execution, "run_deep_search_workflow", fake_deep_search)
    search_context = runtime().context

    async def search(query, call_id):
        return await tool_execution.execute_tool_batch(
            base_state(messages=[AIMessage(content="", tool_calls=[{
                "id": call_id,
                "name": "deep_search",
                "args": {"query": query},
                "type": "tool_call",
            }])]),
            search_context,
            noop_writer,
        )

    first_search, second_search = asyncio.run(_two_searches(search))
    assert searches == 1
    assert first_search["state_patch"]["source_citations"][0]["title"] == "Source"
    assert json.loads(second_search["messages"][0].content)["code"] == (
        "deep_search_turn_limit"
    )

    artifact_context = runtime().context
    artifact_call = lambda call_id, content: {
        "id": call_id,
        "name": "create_artifact",
        "args": {"title": "demo", "kind": "html", "content": content},
        "type": "tool_call",
    }
    first_artifact = asyncio.run(tool_execution.execute_general_tool_batch(
        [artifact_call("artifact-1", "<p>safe</p>")],
        model_id="deepseek-v4-flash",
        context=artifact_context,
        writer=noop_writer,
    ))
    second_artifact = asyncio.run(tool_execution.execute_general_tool_batch(
        [artifact_call("artifact-2", "<p>again</p>")],
        model_id="deepseek-v4-flash",
        context=artifact_context,
        writer=noop_writer,
    ))
    oversized = asyncio.run(tool_execution.execute_general_tool_batch(
        [artifact_call("artifact-large", "x" * (MAX_ARTIFACT_CONTENT_CHARS + 1))],
        model_id="deepseek-v4-flash",
        context=runtime().context,
        writer=noop_writer,
    ))
    assert first_artifact[0].status == "success"
    assert json.loads(second_artifact[0].content)["code"] == (
        "create_artifact_turn_limit"
    )
    assert json.loads(oversized[0].content)["code"] == "schema_validation_failed"


async def _two_searches(search):
    return await search("first", "deep-1"), await search("second", "deep-2")


def test_tool_policy_enforces_schema_state_patch_and_timeout(monkeypatch):
    invalid = asyncio.run(tool_execution.execute_general_tool_batch(
        [{
            "id": "invalid",
            "name": "calculate",
            "args": {},
            "type": "tool_call",
        }],
        model_id="deepseek-v4-flash",
        context=runtime().context,
        writer=noop_writer,
    ))
    assert json.loads(invalid[0].content)["code"] == "schema_validation_failed"

    async def fake_deep_search(**_kwargs):
        return {"summary": "ok", "results": []}

    monkeypatch.setattr(tool_execution, "run_deep_search_workflow", fake_deep_search)
    patch_result = asyncio.run(tool_execution.execute_tool_batch(
        base_state(messages=[AIMessage(content="", tool_calls=[
            {
                "id": "patch-1",
                "name": "deep_search",
                "args": {"query": "one"},
                "type": "tool_call",
            },
            {
                "id": "patch-2",
                "name": "deep_search",
                "args": {"query": "two"},
                "type": "tool_call",
            },
        ])]),
        runtime().context,
        noop_writer,
    ))
    assert patch_result["messages"][0].status == "success"
    assert json.loads(patch_result["messages"][1].content)["code"] == (
        "state_patch_conflict"
    )

    async def slow_calculation(expression: str):
        await asyncio.sleep(0.05)
        return {"expression": expression, "result": 2}

    original = TOOL_REGISTRY.get("calculate", "general_agent")
    assert original is not None
    slow_tool = StructuredTool.from_function(
        coroutine=slow_calculation,
        name="calculate",
        description="slow test calculator",
    )
    monkeypatch.setitem(
        TOOL_REGISTRY._policies,
        "calculate",
        replace(original, tool=slow_tool, timeout_seconds=0.01),
    )
    events = []
    timed_out = asyncio.run(tool_execution.execute_general_tool_batch(
        [{
            "id": "slow",
            "name": "calculate",
            "args": {"expression": "1+1"},
            "type": "tool_call",
        }],
        model_id="deepseek-v4-flash",
        context=runtime().context,
        writer=events.append,
    ))
    assert json.loads(timed_out[0].content)["code"] == "tool_timeout"
    assert events[0]["status"] == "timeout"
    assert events[0]["timeoutReason"]


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
    result = asyncio.run(general.GENERAL_AGENT_GRAPH.ainvoke(
        base_state(
            messages=[HumanMessage(content="calculate")],
            supervisor_decision={
                "route": "general_agent",
                "task": "keep calculating",
                "reason": "loop test",
            },
        ),
        context=runtime().context,
    ))
    assert "3 轮工具调用上限" in result["worker_result"]
    trace = result["messages"][1:]
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
    result = asyncio.run(research.RESEARCH_AGENT_GRAPH.ainvoke(
        base_state(supervisor_decision={
            "route": "research_agent",
            "task": "topic",
            "reason": "needs research",
        }),
        context=runtime(search_mode=search_mode).context,
    ))
    assert result["worker_result"] == "research brief [[cite:1]]"
    assert [type(message).__name__ for message in result["messages"]] == [
        "HumanMessage", "AIMessage", "ToolMessage"
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


def test_reasoner_finalization_strips_tool_protocol_but_keeps_tool_evidence(
    monkeypatch,
):
    class FakeReasonerFinal:
        async def astream(self, messages):
            assert not any(isinstance(message, ToolMessage) for message in messages)
            assert not any(
                isinstance(message, AIMessage) and message.tool_calls
                for message in messages
            )
            assert any("created" in str(message.content) for message in messages)
            yield AIMessageChunk(content="artifact ready")

    monkeypatch.setattr(
        graph_model,
        "create_deepseek_chat",
        lambda _model: FakeReasonerFinal(),
    )
    result = asyncio.run(supervisor.supervisor_finalize_node(base_state(
        model_id="deepseek-reasoner",
        messages=[
            HumanMessage(content="生成 PDF"),
            AIMessage(content="", tool_calls=[{
                "id": "artifact-call",
                "name": "create_artifact",
                "args": {"title": "PDF", "kind": "html", "content": "<h1>PDF</h1>"},
                "type": "tool_call",
            }]),
            ToolMessage(
                content='{"ok":true,"status":"created"}',
                tool_call_id="artifact-call",
                name="create_artifact",
            ),
        ],
        supervisor_decision={
            "route": "general_agent",
            "task": "生成 PDF",
            "reason": "artifact",
        },
        worker_result="工件已创建",
    ), noop_writer))

    assert result["messages"][0].content == "artifact ready"


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


def test_deep_search_runner_invokes_the_compiled_subgraph(monkeypatch):
    class FakeDeepSearchGraph:
        async def ainvoke(self, input_state):
            assert input_state["query"] == "topic"
            return {
                "query": "topic",
                "queries": ["topic"],
                "summary": "brief",
                "results": [],
            }

    monkeypatch.setattr(deep_search, "DEEP_SEARCH_GRAPH", FakeDeepSearchGraph())
    result = asyncio.run(deep_search.run_deep_search_workflow(
        query="topic",
        focus="",
        model_id="deepseek-v4-flash",
    ))

    assert result["summary"] == "brief"


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
