"""Checkpoint 热读不能破坏 LangGraph 的持久化、回放和租户隔离语义。"""

from copy import deepcopy

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph

from app.checkpointing import CachedCheckpointSaver
from app.observability import TraceCollector, bind_trace


def _graph_for(saver):
    async def respond(state: MessagesState):
        return {"messages": [AIMessage(content=f"seen:{len(state['messages'])}")]}

    builder = StateGraph(MessagesState)
    builder.add_node("respond", respond)
    builder.add_edge(START, "respond")
    builder.add_edge("respond", END)
    return builder.compile(checkpointer=saver)


@pytest.mark.asyncio
async def test_checkpoint_cache_isolates_scope_and_bypasses_history(tmp_path):
    path = str(tmp_path / "hot-checkpoints.db")
    seed_config = {"configurable": {"thread_id": "thread-one"}}
    scope_a = {"configurable": {
        "thread_id": "thread-one",
        "checkpoint_cache_scope": "stream-a",
    }}
    scope_b = {"configurable": {
        "thread_id": "thread-one",
        "checkpoint_cache_scope": "stream-b",
    }}
    namespaced = {"configurable": {
        "thread_id": "thread-one",
        "checkpoint_ns": "worker",
        "checkpoint_cache_scope": "stream-a",
    }}
    other_thread = {"configurable": {
        "thread_id": "thread-two",
        "checkpoint_cache_scope": "stream-a",
    }}

    async with AsyncSqliteSaver.from_conn_string(path) as durable:
        await durable.setup()
        saver = CachedCheckpointSaver(durable)
        await _graph_for(saver).ainvoke(
            {"messages": [HumanMessage(content="seed")]}, seed_config
        )
        original_get = durable.aget_tuple
        durable_reads = 0

        async def counted_get(config):
            nonlocal durable_reads
            durable_reads += 1
            return await original_get(config)

        durable.aget_tuple = counted_get  # type: ignore[method-assign]
        collector = TraceCollector(
            conversation_id="thread-one",
            user_message_id="message-one",
            model="deepseek-v4-flash",
            search_mode="auto",
        )
        with bind_trace(collector):
            first = await saver.aget_tuple(scope_a)
            second = await saver.aget_tuple(scope_a)
            assert first is not None and second is not None
            assert durable_reads == 1

            original_values = deepcopy(first.checkpoint["channel_values"])
            second.checkpoint["channel_values"].clear()
            third = await saver.aget_tuple(scope_a)
            assert third is not None
            assert third.checkpoint["channel_values"] == original_values

            # 不同 stream 不共享对象；指定 checkpoint 和 history 永远绕过热值。
            await saver.aget_tuple(scope_b)
            assert await saver.aget_tuple(namespaced) is None
            assert await saver.aget_tuple(namespaced) is None
            assert await saver.aget_tuple(other_thread) is None
            assert await saver.aget_tuple(other_thread) is None
            await saver.aget_tuple(first.config)
            history = [item async for item in saver.alist(scope_a, limit=20)]
            assert history

        assert durable_reads == 5
        trace = collector.finish("success")
        assert trace["metrics"]["checkpoint_hot_hits"] == 4
        assert trace["metrics"]["checkpoint_durable_reads"] == 5
        assert trace["metrics"]["checkpoint_history_reads"] == 1

        await saver.aclear_scope("stream-a")
        await saver.aget_tuple(scope_a)
        assert durable_reads == 6


@pytest.mark.asyncio
async def test_checkpoint_write_invalidates_all_scopes_and_restart_recovers(tmp_path):
    path = str(tmp_path / "restart-checkpoints.db")
    config = {"configurable": {
        "thread_id": "restartable",
        "checkpoint_cache_scope": "reader",
    }}

    async with AsyncSqliteSaver.from_conn_string(path) as durable:
        await durable.setup()
        saver = CachedCheckpointSaver(durable)
        graph = _graph_for(saver)
        first_result = await graph.ainvoke(
            {"messages": [HumanMessage(content="first")]}, config
        )
        before = await saver.aget_tuple(config)
        assert before is not None
        await graph.ainvoke(
            {"messages": [HumanMessage(content="second")]},
            {"configurable": {
                "thread_id": "restartable",
                "checkpoint_cache_scope": "writer",
            }},
        )
        latest = await saver.aget_tuple(config)
        assert latest is not None
        assert latest.checkpoint["id"] != before.checkpoint["id"]

    async with AsyncSqliteSaver.from_conn_string(path) as reopened:
        await reopened.setup()
        resumed = await _graph_for(CachedCheckpointSaver(reopened)).ainvoke(
            {"messages": [HumanMessage(content="third")]}, config
        )
        assert len(resumed["messages"]) == len(first_result["messages"]) + 4


@pytest.mark.asyncio
async def test_chat_preflight_and_graph_start_use_one_durable_read(tmp_path):
    path = str(tmp_path / "chat-pattern.db")
    config = {"configurable": {
        "thread_id": "chat-pattern",
        "checkpoint_cache_scope": "stream-one",
    }}
    async with AsyncSqliteSaver.from_conn_string(path) as durable:
        await durable.setup()
        saver = CachedCheckpointSaver(durable)
        graph = _graph_for(saver)
        await graph.ainvoke({"messages": [HumanMessage(content="first")]}, config)
        await saver.aclear_scope("stream-one")

        original_get = durable.aget_tuple
        durable_reads = 0

        async def counted_get(read_config):
            nonlocal durable_reads
            durable_reads += 1
            return await original_get(read_config)

        durable.aget_tuple = counted_get  # type: ignore[method-assign]
        snapshot = await graph.aget_state(config)
        assert snapshot.values
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="second")]}, config
        )

        assert durable_reads == 1
        assert len(result["messages"]) == 4
