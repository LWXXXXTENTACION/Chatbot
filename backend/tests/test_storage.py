import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.cache import (
    CACHE_POLICIES,
    CacheLookup,
    MultiLayerCache,
    ToolCache,
    tool_cache_key,
)
from app.database.messages import (
    db_message_to_langchain,
    persist_graph_messages,
    persist_user_message,
)
from app.database.migrate import run_migrations
from app.database.models import Base, Conversation, Message, ToolCacheEntry, User
from app.middleware.rate_limit import InMemoryRateLimiter, RedisRateLimiter
from app.streaming import stream_sse_events


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.set_calls = []
        self.eval_result = [1, 0]
        self.fail = False

    async def get(self, key):
        if self.fail:
            raise ConnectionError("redis down")
        return self.values.get(key)

    async def set(self, key, value, ex):
        if self.fail:
            raise ConnectionError("redis down")
        self.values[key] = value
        self.set_calls.append((key, ex))

    async def eval(self, *_args):
        if self.fail:
            raise ConnectionError("redis down")
        return self.eval_result


@pytest.mark.asyncio
async def test_sse_stream_delivers_terminal_event_when_producer_finishes_first():
    queue = asyncio.Queue()
    await queue.put({"type": "text_delta", "delta": "完成"})
    await queue.put({"type": "done", "messageId": "message-1"})
    producer = asyncio.create_task(asyncio.sleep(0))
    await producer

    chunks = [
        chunk.decode()
        async for chunk in stream_sse_events(queue, producer)
    ]

    assert len(chunks) == 2
    assert '"type": "text_delta"' in chunks[0]
    assert '"type": "done"' in chunks[1]
    assert queue.empty()


@pytest.mark.asyncio
async def test_tool_cache_is_stable_versioned_and_fail_open():
    redis = FakeRedis()
    cache = ToolCache(redis)
    args = {"query": "  LangGraph   persistence ", "focus": "docs"}
    key = tool_cache_key("deep_search", args, model_id="deepseek-v4-flash")
    assert key == tool_cache_key(
        "deep_search",
        {"focus": "docs", "query": "LangGraph persistence"},
        model_id="deepseek-v4-flash",
    )
    assert tool_cache_key(
        "deep_search", {"query": "topic"}, model_id="m"
    ) == tool_cache_key(
        "deep_search", {"query": "topic", "focus": ""}, model_id="m"
    )
    assert tool_cache_key(
        "get_weather", {"city": " New   York "}
    ) == tool_cache_key("get_weather", {"city": "new york"})
    assert tool_cache_key(
        "calculate", {"expression": "1 + 2 * 3"}
    ) == tool_cache_key("calculate", {"expression": "1+2*3"})
    assert tool_cache_key(
        "web_search", {"query": "LangGraph"}
    ) == tool_cache_key(
        "web_search", {"query": "LangGraph", "max_results": 5}
    )

    assert await cache.get("deep_search", args, model_id="deepseek-v4-flash") == CacheLookup(False)
    value = {"summary": "answer", "results": [{"url": "https://example.com"}]}
    await cache.put("deep_search", args, value, model_id="deepseek-v4-flash")
    assert redis.set_calls[-1] == (key, CACHE_POLICIES["deep_search"].ttl_seconds)
    assert (await cache.get("deep_search", args, model_id="deepseek-v4-flash")).value == value

    await cache.put("create_artifact", {"content": "x"}, {"ok": True})
    assert len(redis.set_calls) == 1
    await cache.put("get_weather", {"city": "上海"}, {"error": "upstream"})
    assert len(redis.set_calls) == 1
    assert CACHE_POLICIES["get_weather"].ttl_seconds == 60
    assert CACHE_POLICIES["calculate"].ttl_seconds == 86_400
    assert CACHE_POLICIES["web_search"].ttl_seconds == 300
    redis.fail = True
    assert not (await cache.get("get_weather", {"city": "北京"})).hit


@pytest.mark.asyncio
async def test_multi_layer_cache_promotes_l2_and_l3_hits(tmp_path):
    """L2/L3 命中必须向上回填，第二次读取才能稳定走进程内热路径。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cache.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    redis = FakeRedis()
    args = {"city": "上海"}
    value = {"temperature": 28, "condition": "晴"}
    writer = MultiLayerCache(redis, sessions)
    await writer.put("get_weather", args, value)

    async with sessions() as db:
        row = await db.get(ToolCacheEntry, tool_cache_key("get_weather", args))
        assert row is not None
        assert row.value == value

    # 新实例没有 L1，但复用 Redis，因此第一次命中 L2，第二次晋升为 L1。
    from_l2 = MultiLayerCache(redis, sessions)
    assert (await from_l2.get("get_weather", args)).layer == "l2"
    assert (await from_l2.get("get_weather", args)).layer == "l1"

    # 模拟 Redis 数据丢失/重启：L3 命中后应同时恢复 Redis 和当前进程 L1。
    redis.values.clear()
    redis.set_calls.clear()
    from_l3 = MultiLayerCache(redis, sessions)
    l3_lookup = await from_l3.get("get_weather", args)
    assert l3_lookup == CacheLookup(hit=True, value=value, layer="l3")
    assert redis.set_calls
    assert (await from_l3.get("get_weather", args)).layer == "l1"

    # Redis 故障只跳过 L2，不得阻断后续数据库回源。
    redis.fail = True
    fail_open = MultiLayerCache(redis, sessions)
    assert (await fail_open.get("get_weather", args)).layer == "l3"
    await engine.dispose()


@pytest.mark.asyncio
async def test_multi_layer_cache_l1_is_bounded_and_expired_l3_is_deleted(tmp_path):
    cache = MultiLayerCache(None, l1_max_entries=2)
    await cache.put("get_weather", {"city": "北京"}, {"temperature": 20})
    await cache.put("get_weather", {"city": "上海"}, {"temperature": 25})
    await cache.put("get_weather", {"city": "广州"}, {"temperature": 30})
    assert cache.l1_size == 2
    assert not (await cache.get("get_weather", {"city": "北京"})).hit
    assert (await cache.get("get_weather", {"city": "广州"})).layer == "l1"

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'expired.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    key = tool_cache_key("calculate", {"expression": "1+1"})
    async with sessions() as db:
        db.add(ToolCacheEntry(
            cache_key=key,
            tool_name="calculate",
            value={"result": 2},
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        ))
        await db.commit()

    l3_only = MultiLayerCache(None, sessions, l1_max_entries=0)
    assert not (await l3_only.get("calculate", {"expression": "1+1"})).hit
    async with sessions() as db:
        assert await db.get(ToolCacheEntry, key) is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_rate_limiter_uses_redis_and_falls_back():
    redis = FakeRedis()
    limiter = RedisRateLimiter(redis)
    assert await limiter.consume("user") == (True, 0)
    redis.eval_result = [0, 3]
    assert await limiter.consume("user") == (False, 3)

    redis.fail = True
    assert await limiter.consume("fallback-user") == (True, 0)
    local = InMemoryRateLimiter()
    for _ in range(20):
        assert (await local.consume("limited"))[0]
    allowed, retry_after = await local.consume("limited")
    assert not allowed
    assert retry_after >= 1


@pytest.mark.asyncio
async def test_message_persistence_round_trip_has_no_blank_tool_rows(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'messages.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sessions() as db:
        db.add(User(id="u" * 32, username="tester", password_hash="hash"))
        db.add(Conversation(id="c" * 32, user_id="u" * 32, title="test"))
        await db.commit()

        human = HumanMessage(content="天气？", id="h" * 32)
        await persist_user_message(db, "c" * 32, human)
        call = {"id": "call-1", "name": "get_weather", "args": {"city": "北京"}, "type": "tool_call"}
        ai_call = AIMessage(content="", tool_calls=[call], id="a" * 32)
        tool = ToolMessage(
            content=json.dumps({"tempC": 24}),
            tool_call_id="call-1",
            name="get_weather",
            id="t" * 32,
        )
        final = AIMessage(content="北京 24°C", id="f" * 32)
        await persist_graph_messages(db, "c" * 32, [ai_call, tool, final])

        rows = (await db.execute(
            select(Message)
            .options(selectinload(Message.parts))
            .where(Message.conversation_id == "c" * 32)
            .order_by(Message.sequence)
        )).scalars().all()
        assert [row.role for row in rows] == ["user", "assistant", "assistant"]
        assert [row.sequence for row in rows] == [0, 1, 2]
        rebuilt = [item for row in rows for item in db_message_to_langchain(row)]
        assert [type(item).__name__ for item in rebuilt] == [
            "HumanMessage",
            "AIMessage",
            "ToolMessage",
            "AIMessage",
        ]
        assert json.loads(rebuilt[2].content)["tempC"] == 24
    await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_checkpointer_persists_and_isolates_threads(tmp_path):
    checkpoint_path = str(tmp_path / "checkpoints.db")

    async def respond(state: MessagesState):
        return {"messages": [AIMessage(content=f"seen:{len(state['messages'])}")]}

    def graph_for(saver):
        builder = StateGraph(MessagesState)
        builder.add_node("respond", respond)
        builder.add_edge(START, "respond")
        builder.add_edge("respond", END)
        return builder.compile(checkpointer=saver)

    async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as saver:
        graph = graph_for(saver)
        one = {"configurable": {"thread_id": "one"}}
        two = {"configurable": {"thread_id": "two"}}
        assert len((await graph.ainvoke({"messages": [HumanMessage(content="a")]}, one))["messages"]) == 2
        assert len((await graph.ainvoke({"messages": [HumanMessage(content="b")]}, one))["messages"]) == 4
        assert len((await graph.ainvoke({"messages": [HumanMessage(content="x")]}, two))["messages"]) == 2

    async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as saver:
        graph = graph_for(saver)
        resumed = await graph.ainvoke(
            {"messages": [HumanMessage(content="c")]},
            {"configurable": {"thread_id": "one"}},
        )
        assert len(resumed["messages"]) == 6


def test_legacy_database_is_adopted_and_migrated(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE users (id VARCHAR(32) PRIMARY KEY, username VARCHAR(64) NOT NULL,
          password_hash VARCHAR(128) NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL);
        CREATE TABLE conversations (id VARCHAR(32) PRIMARY KEY, user_id VARCHAR(32) NOT NULL,
          title VARCHAR(128) NOT NULL, model VARCHAR(32) NOT NULL,
          created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL);
        CREATE TABLE messages (id VARCHAR(32) PRIMARY KEY, conversation_id VARCHAR(32) NOT NULL,
          role VARCHAR(16) NOT NULL, created_at DATETIME NOT NULL);
        CREATE TABLE message_parts (id VARCHAR(32) PRIMARY KEY, message_id VARCHAR(32) NOT NULL,
          type VARCHAR(32) NOT NULL, text TEXT, tool_call_id VARCHAR(64), tool_state VARCHAR(24),
          tool_input JSON, tool_output JSON, tool_error TEXT, position INTEGER NOT NULL);
        INSERT INTO users VALUES ('u', 'tester', 'hash', '2026-01-01', '2026-01-01');
        INSERT INTO conversations VALUES ('c', 'u', 'test', 'deepseek-v4-flash', '2026-01-01', '2026-01-01');
        INSERT INTO messages VALUES ('m1', 'c', 'user', '2026-01-01');
        INSERT INTO messages VALUES ('m2', 'c', 'assistant', '2026-01-02');
    """)
    connection.commit()
    connection.close()

    run_migrations(f"sqlite:///{path}")
    run_migrations(f"sqlite:///{path}")

    connection = sqlite3.connect(path)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(messages)")}
    assert "sequence" in columns
    assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2
    assert connection.execute("SELECT message_sequence FROM conversations").fetchone()[0] == 2
    assert connection.execute("SELECT sequence FROM messages ORDER BY sequence").fetchall() == [(0,), (1,)]
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='tool_cache_entries'"
    ).fetchone() == ("tool_cache_entries",)
    connection.close()
