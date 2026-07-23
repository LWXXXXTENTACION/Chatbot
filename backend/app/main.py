"""FastAPI 应用装配入口。

这个文件只管理“进程级资源”的创建和释放，不处理聊天业务：数据库迁移、
LangGraph checkpointer、编译后的父图、Redis 客户端和 SSE Registry 都在
lifespan 中创建，再通过 ``app.state`` 注入路由。
"""

import asyncio
import logging
import os
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from app.config import (
    AUTO_MIGRATE,
    CHECKPOINT_DB_PATH,
    CONTEXT_EMBED_ALLOW_DOWNLOAD,
    CONTEXT_EMBED_MODEL,
    CONTEXT_INDEX_COLLECTION,
    CONTEXT_INDEX_ENABLED,
    CONTEXT_INDEX_PATH,
    CONTEXT_INDEX_VERSION,
    REDIS_ENABLED,
    REDIS_URL,
)
from app.cache import MultiLayerCache, create_redis_client
from app.checkpointing import CachedCheckpointSaver
from app.context_index import ContextIndexService
from app.database.migrate import run_migrations
from app.graph.builder import build_graph
from app.middleware.rate_limit import RedisRateLimiter
from app.streaming import SSEStreamRegistry

logger = logging.getLogger("chatbot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """在一个生命周期中成对管理所有异步资源，避免连接泄漏。"""
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    from app.database.engine import async_session, engine
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    if AUTO_MIGRATE:
        await asyncio.to_thread(run_migrations)
        logger.info("Database migrations applied")

    async with AsyncExitStack() as stack:
        durable_checkpointer = await stack.enter_async_context(
            AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB_PATH)
        )
        await durable_checkpointer.setup()
        # Checkpoint 仍由 SQLite 持久化，包装器只按 stream 隔离最新 head 热读。
        checkpointer = CachedCheckpointSaver(durable_checkpointer)
        redis_client = await create_redis_client(REDIS_URL, enabled=REDIS_ENABLED)
        # 父图是唯一拥有持久化 saver 的 Graph。Worker 子图继承当前调用作用域，
        # 不再各自创建数据库连接或独立 checkpoint namespace。
        app.state.checkpointer = checkpointer
        app.state.graph = build_graph(checkpointer=checkpointer)
        app.state.context_index = ContextIndexService(
            enabled=CONTEXT_INDEX_ENABLED,
            path=CONTEXT_INDEX_PATH,
            collection=CONTEXT_INDEX_COLLECTION,
            embed_model_name=CONTEXT_EMBED_MODEL,
            allow_model_download=CONTEXT_EMBED_ALLOW_DOWNLOAD,
            index_version=CONTEXT_INDEX_VERSION,
            session_factory=async_session,
        )
        # 工具结果按 L1 内存 → L2 Redis → L3 数据库读取；Redis 未启用时仍保留
        # 当前进程热缓存和数据库冷缓存，三层中的单层故障不会阻断聊天主链。
        app.state.tool_cache = MultiLayerCache(redis_client, async_session)
        app.state.rate_limiter = RedisRateLimiter(redis_client)
        app.state.stream_registry = SSEStreamRegistry()
        app.state.stream_registry.start()
        app.state.redis = redis_client
        try:
            yield
        finally:
            await app.state.stream_registry.close()
            await app.state.context_index.close()
            if redis_client is not None:
                await redis_client.aclose()
            await engine.dispose()


app = FastAPI(
    title="DeepSeek Chatbot Backend",
    version="0.1.0",
    lifespan=lifespan,
)

# Register routers
from app.routers.auth import router as auth_router
from app.routers.conversations import router as conversations_router
from app.routers.chat import router as chat_router
from app.routers.observability import router as observability_router
app.include_router(auth_router)
app.include_router(conversations_router)
app.include_router(chat_router)
app.include_router(observability_router)
