"""聊天 API：把数据库、LangGraph 和可续传 SSE 串成一条清晰的数据链。

调用顺序是：鉴权与参数校验 → 加载历史 → 执行父 Graph → 转发 custom 事件
→ 保存新增消息/运行轨迹 → 发布 done。路由只负责边界编排，Agent 的任务划分、
工具循环和 Artifact 工作流都在 ``app.graph`` / ``app.agents`` 中声明。
"""

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response, StreamingResponse
from langchain_core.messages import BaseMessage, HumanMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import (
    DEEPSEEK_API_KEY,
    DEFAULT_MODEL,
    DEFAULT_SYSTEM_PROMPT,
    DeepSeekModelId,
    validate_model,
)
from app.database.engine import async_session, get_db
from app.database.messages import (
    db_message_to_langchain,
    persist_graph_messages,
    persist_user_message,
)
from app.database.models import Conversation, Message, User
from app.database.observability import persist_run_trace
from app.graph.builder import build_graph
from app.graph.context import AgentRuntimeContext, SearchMode
from app.graph.message_conversion import ui_messages_to_langchain
from app.graph.state import AgentInput
from app.middleware.auth import get_current_user
from app.middleware.rate_limit import check_rate_limit
from app.observability import TraceCollector, bind_trace
from app.schemas.chat import ChatRequest
from app.streaming import ResumableSSEStream, SSEStreamRegistry

logger = logging.getLogger("chatbot.chat")
router = APIRouter(prefix="/api/chat", tags=["chat"])


# ——— Models ———


@router.get("/models")
async def list_models():
    """Return available DeepSeek models (no auth required)."""
    from app.llm.models import DEEPSEEK_MODELS

    return [
        {
            "id": m.id,
            "name": m.name,
            "badge": m.badge,
            "description": m.description,
            "deprecated": m.deprecated,
        }
        for m in DEEPSEEK_MODELS
    ]


# ——— Graph runner ———


async def _run_graph_and_stream(
    graph: Any,
    input_messages: list[BaseMessage],
    new_user_message_id: str | None,
    model_id: DeepSeekModelId,
    system_prompt: str,
    send_event: Any,
    user_id: str,
    conversation_id: str,
    tool_cache: Any | None,
    context_index: Any | None,
    search_mode: SearchMode,
    checkpoint_cache_scope: str,
) -> list[BaseMessage]:
    """执行一次标准 LangGraph 流，并返回本轮新产生的消息。

    ``values`` 用于取得最终状态，``custom`` 是面向 UI 的稳定事件协议。
    开启 ``subgraphs=True`` 后子图也会产生 values 快照，因此这里只接受根命名空间，
    防止把 Worker 子图的局部 State 错当成父图最终 State。
    """
    initial_state: AgentInput = {
        "messages": input_messages,
        "model_id": model_id,
        "system_prompt": system_prompt,
        "user_id": user_id,
        "conversation_id": conversation_id,
    }
    config: dict[str, Any] = {"recursion_limit": 12}
    if conversation_id:
        config["configurable"] = {
            "thread_id": conversation_id,
            "checkpoint_cache_scope": checkpoint_cache_scope,
        }
    final_state: dict[str, Any] | None = None
    async for part in graph.astream(
        initial_state,
        config=config,
        context=AgentRuntimeContext(
            tool_cache=tool_cache,
            search_mode=search_mode,
            context_index=context_index,
        ),
        stream_mode=["values", "custom"],
        subgraphs=True,
        version="v2",
    ):
        if part["type"] == "custom":
            await send_event(part["data"])
        elif part["type"] == "values" and not part.get("ns"):
            # 开启 subgraphs 后也会收到子图快照；最终状态只取父图根命名空间。
            final_state = part["data"]
    if final_state is None:
        raise RuntimeError("LangGraph completed without a final state")
    if final_state.get("error"):
        raise RuntimeError(str(final_state["error"]))

    all_messages: list[BaseMessage] = final_state.get("messages", [])
    if new_user_message_id:
        for index, message in enumerate(all_messages):
            if str(message.id or "") == new_user_message_id:
                return all_messages[index + 1:]
    return all_messages[len(input_messages):]


# ——— SSE Endpoint ———


@router.delete("/stream/{stream_id}")
async def cancel_chat_stream(
    stream_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Cancel an explicit user stop without treating transport loss as a stop."""
    registry: SSEStreamRegistry = request.app.state.stream_registry
    stream = registry.get(stream_id, str(current_user.id))
    if stream is None:
        return Response(status_code=404)
    await stream.cancel()
    await stream.publish({
        "type": "error",
        "message": "生成已停止",
        "code": "CLIENT_CANCELLED",
    })
    return Response(status_code=204)


@router.get("/stream/{stream_id}/status")
async def chat_stream_status(
    stream_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """无副作用地探测流是否仍存在，供“尚未收到首事件”的刷新场景使用。

    此时 Last-Event-ID 还是 0；若后端已经重启，浏览器盲目重发 POST 会创建重复任务。
    所以前端先探测：204 表示可继续订阅，404 表示放弃本地会话且绝不重跑。
    """
    registry: SSEStreamRegistry = request.app.state.stream_registry
    if registry.get(stream_id, str(current_user.id)) is None:
        return Response(status_code=404)
    return Response(status_code=204)


@router.post("/stream")
async def chat_stream(
    request: Request,
    body: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _ratelimit: None = Depends(check_rate_limit),
):
    """创建或续订一个经过鉴权的 SSE 聊天流。"""
    stream_id = body.stream_id or uuid.uuid4().hex
    last_event_id_raw = request.headers.get("last-event-id", "").strip()
    if last_event_id_raw and not last_event_id_raw.isdecimal():
        return Response(
            content=json.dumps({"error": "Last-Event-ID 必须是非负整数"}),
            status_code=400,
            media_type="application/json",
        )
    last_event_id = int(last_event_id_raw or "0")
    registry: SSEStreamRegistry = request.app.state.stream_registry
    existing_stream = registry.get(stream_id, str(current_user.id))
    if existing_stream is not None:
        # 相同 stream_id 永远只订阅已有日志，不会再次执行 Graph。
        return StreamingResponse(
            existing_stream.subscribe(last_event_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "X-Stream-ID": stream_id,
            },
        )
    if registry.has(stream_id):
        return Response(
            content=json.dumps({"error": "流不存在"}),
            status_code=404,
            media_type="application/json",
        )
    if last_event_id > 0:
        return Response(
            content=json.dumps({"error": "续传流已过期", "code": "STREAM_NOT_FOUND"}),
            status_code=409,
            media_type="application/json",
        )

    if not DEEPSEEK_API_KEY:
        return Response(
            content=json.dumps({"error": "未配置 DEEPSEEK_API_KEY"}),
            status_code=500,
            media_type="application/json",
        )

    model_id = validate_model(body.model or DEFAULT_MODEL)
    system = body.system or DEFAULT_SYSTEM_PROMPT

    input_messages: list[BaseMessage] = []
    new_user_msg: HumanMessage | None = None
    collector: TraceCollector | None = None
    conversation_id = body.conversation_id or ""
    graph = build_graph()

    if body.conversation_id and body.new_message:
        # 推荐模式：后端从数据库加载可信历史，再追加本轮用户消息。
        result = await db.execute(
            select(Conversation)
            .options(
                selectinload(Conversation.messages).selectinload(Message.parts)
            )
            .where(
                Conversation.id == body.conversation_id,
                Conversation.user_id == current_user.id,
            )
        )
        conv = result.scalar_one_or_none()
        if not conv:
            return Response(
                content=json.dumps({"error": "对话不存在"}),
                status_code=404,
                media_type="application/json",
            )

        history: list[BaseMessage] = []
        for msg in conv.messages or []:
            history.extend(db_message_to_langchain(msg))

        # Add new user message
        nm = body.new_message
        user_text = nm.content
        if not user_text and nm.parts:
            for p in nm.parts:
                if isinstance(p, dict) and p.get("type") == "text":
                    user_text += p.get("text", "")
        new_user_msg = HumanMessage(content=user_text, id=uuid.uuid4().hex)

        graph = request.app.state.graph
        checkpoint_config = {"configurable": {
            "thread_id": conversation_id,
            "checkpoint_cache_scope": stream_id,
        }}
        collector = TraceCollector(
            conversation_id=conversation_id,
            user_message_id=str(new_user_msg.id),
            model=model_id,
            search_mode=body.search_mode,
        )
        # 预检和 Graph 使用完全相同的 config；Saver 直接按 stream_id 找到热 head。
        with bind_trace(collector):
            snapshot = await graph.aget_state(checkpoint_config)
        checkpoint_messages = (
            snapshot.values.get("messages", []) if snapshot.values else []
        )
        checkpoint_synced = bool(snapshot.values) and (
            (not history and not checkpoint_messages)
            or (
                bool(history)
                and bool(checkpoint_messages)
                and str(history[-1].id or "") == str(checkpoint_messages[-1].id or "")
            )
        )
        # SQLite 消息记录是持久事实源。如果 checkpoint 与它不一致（例如开发期
        # 清库、迁移或旧版本 State），删除该线程快照并用完整数据库历史重新播种。
        if snapshot.values and not checkpoint_synced:
            await request.app.state.checkpointer.adelete_thread(conversation_id)
        input_messages = (
            [new_user_msg]
            if checkpoint_synced
            else [*history, new_user_msg]
        )

        await persist_user_message(db, conversation_id, new_user_msg)

    elif body.messages:
        # 兼容旧客户端：接收完整 messages；新代码应优先使用 conversation_id。
        input_messages = ui_messages_to_langchain(body.messages)
    else:
        return Response(
            content=json.dumps({"error": "缺少对话内容"}),
            status_code=400,
            media_type="application/json",
        )

    if collector is None:
        collector = TraceCollector(
            conversation_id=conversation_id,
            user_message_id=str(new_user_msg.id) if new_user_msg else "",
            model=model_id,
            search_mode=body.search_mode,
        )

    # Graph 生产任务不属于某一条 HTTP 连接。浏览器刷新或切换对话只会断开订阅；
    # 用同一个 stream_id 重连时从日志补读，不会重新运行 Graph。
    stream = ResumableSSEStream(stream_id, str(current_user.id))
    registry.register(stream)

    async def send_event(event: dict[str, Any]) -> None:
        # 所有 Graph custom 事件都经过同一个入口，便于观测与 SSE 序号保持一致。
        collector.observe_event(event)
        await stream.publish(event)

    checkpointer = getattr(request.app.state, "checkpointer", None)
    tool_cache = getattr(request.app.state, "tool_cache", None)
    context_index = getattr(request.app.state, "context_index", None)

    async def invalidate_checkpoint() -> None:
        if checkpointer is not None and conversation_id:
            try:
                await checkpointer.adelete_thread(conversation_id)
            except Exception:
                logger.exception("Failed to invalidate checkpoint for %s", conversation_id)

    async def persist_trace_safely(trace: dict[str, Any]) -> None:
        """Observability must never turn a completed answer into a failed run."""
        if not conversation_id or new_user_msg is None:
            return
        try:
            async with async_session() as trace_db:
                await persist_run_trace(trace_db, str(new_user_msg.id), trace)
        except Exception:
            logger.exception("Failed to persist trace %s", trace.get("run_id"))

    async def run_and_persist() -> None:
        """后台生产者：完整执行 Graph，并在终态前完成消息与轨迹持久化。"""
        try:
            with bind_trace(collector):
                new_messages = await _run_graph_and_stream(
                    graph,
                    input_messages,
                    str(new_user_msg.id) if new_user_msg else None,
                    model_id,
                    system,
                    send_event,
                    str(current_user.id),
                    conversation_id,
                    tool_cache,
                    context_index,
                    body.search_mode,
                    stream_id,
                )
            if conversation_id and new_messages:
                async with async_session() as persistence_db:
                    await persist_graph_messages(
                        persistence_db,
                        conversation_id,
                        new_messages,
                    )
            trace = collector.finish("success")
            await persist_trace_safely(trace)
            await send_event({"type": "trace_summary", "trace": trace})
            done_id = next(
                (str(message.id) for message in reversed(new_messages) if message.id),
                "",
            )
            await send_event({"type": "done", "messageId": done_id})
        except asyncio.CancelledError:
            logger.info("Graph run cancelled for conversation %s", conversation_id)
            trace = collector.finish("cancelled", error_code="CLIENT_CANCELLED")
            await asyncio.shield(persist_trace_safely(trace))
            await invalidate_checkpoint()
            raise
        except Exception as exc:
            logger.exception("Graph or persistence failure")
            trace = collector.finish("error", error_code=type(exc).__name__)
            await persist_trace_safely(trace)
            await invalidate_checkpoint()
            await send_event({
                "type": "error",
                "message": f"生成或保存回复时发生错误: {exc}",
                "code": "STREAM_ERROR",
            })
        finally:
            # 正常/异常终态都主动释放 stream 热值；TTL 只处理进程被打断的情况。
            if checkpointer is not None:
                try:
                    await checkpointer.aclear_scope(stream_id)
                except Exception:
                    logger.exception("Failed to clear checkpoint scope %s", stream_id)

    graph_task = asyncio.create_task(run_and_persist())
    stream.attach_producer(graph_task)

    return StreamingResponse(
        stream.subscribe(last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "X-Stream-ID": stream_id,
        },
    )
