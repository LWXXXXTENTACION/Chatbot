"""
Chat streaming router with authentication.
Uses LangGraph for agent orchestration, streams results via SSE.
"""

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response, StreamingResponse
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
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
from app.graph.builder import build_graph
from app.graph.context import AgentRuntimeContext, SearchMode
from app.graph.state import AgentState
from app.middleware.auth import get_current_user
from app.middleware.rate_limit import check_rate_limit
from app.schemas.chat import ChatRequest
from app.streaming import stream_sse_events

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


# ——— Message conversion (from frontend dicts to LangChain) ———


def _extract_text(msg: dict[str, Any]) -> str:
    """Extract text from a message dict, checking both content and parts fields."""
    content = msg.get("content", "")
    parts = msg.get("parts", None)

    if content and str(content).strip():
        return str(content)

    if parts and isinstance(parts, list):
        text_bits: list[str] = []
        for p in parts:
            if isinstance(p, dict) and p.get("type") == "text":
                text_bits.append(p.get("text", ""))
        return "\n".join(text_bits)

    return ""


def _convert_messages(raw_messages: list[dict[str, Any]]) -> list[BaseMessage]:
    """Convert raw message dicts from the frontend into LangChain messages."""
    result: list[BaseMessage] = []
    for msg in raw_messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        parts = msg.get("parts", None)

        if role == "system":
            text = _extract_text(msg)
            result.append(SystemMessage(content=text if text else str(content)))
        elif role == "user":
            text = _extract_text(msg)
            result.append(HumanMessage(content=text if text else str(content)))
        elif role == "assistant":
            if parts and isinstance(parts, list):
                text_parts: list[str] = []
                tool_calls: list[dict[str, Any]] = []
                tool_results: list[ToolMessage] = []
                for part in parts:
                    part_type = part.get("type", "")
                    if part_type == "text":
                        text_parts.append(part.get("text", ""))
                    elif part_type == "reasoning":
                        pass  # skip reasoning parts in conversion
                    elif part_type and part_type.startswith("tool-"):
                        tool_name = part_type[5:]
                        tool_input = part.get("input", {})
                        tool_call_id = part.get("toolCallId", "")
                        tool_calls.append({
                            "id": tool_call_id,
                            "name": tool_name,
                            "args": tool_input,
                            "type": "tool_call",
                        })
                        if part.get("state") == "output-available" and part.get("output"):
                            tool_results.append(ToolMessage(
                                content=json.dumps(part["output"], ensure_ascii=False),
                                tool_call_id=tool_call_id,
                            ))
                ai_msg = AIMessage(content="\n".join(text_parts) if text_parts else "")
                if tool_calls:
                    ai_msg.tool_calls = tool_calls  # type: ignore[assignment]
                result.append(ai_msg)
                result.extend(tool_results)
            else:
                text = str(content) if isinstance(content, str) else ""
                if isinstance(content, list):
                    text_bits = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                    text = "\n".join(text_bits)
                result.append(AIMessage(content=text))
        elif role == "tool":
            result.append(ToolMessage(
                content=json.dumps(content) if isinstance(content, dict) else str(content),
                tool_call_id=msg.get("toolCallId", ""),
            ))
    return result


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
    search_mode: SearchMode,
) -> list[BaseMessage]:
    """Run one graph turn and return messages created after the new user input."""
    initial_state: AgentState = {
        "messages": input_messages,
        "model_id": model_id,
        "system_prompt": system_prompt,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "source_citations": [],
        "retrieved_docs": "",
        "search_iteration": 0,
        "search_history": [],
        "error": None,
    }
    config: dict[str, Any] = {"recursion_limit": 12}
    if conversation_id:
        config["configurable"] = {"thread_id": conversation_id}
    final_state = await graph.ainvoke(
        initial_state,
        config=config,
        context=AgentRuntimeContext(
            stream_callback=send_event,
            tool_cache=tool_cache,
            search_mode=search_mode,
        ),
    )
    if final_state.get("error"):
        raise RuntimeError(str(final_state["error"]))

    all_messages: list[BaseMessage] = final_state.get("messages", [])
    if new_user_message_id:
        for index, message in enumerate(all_messages):
            if str(message.id or "") == new_user_message_id:
                return all_messages[index + 1:]
    return all_messages[len(input_messages):]


# ——— SSE Endpoint ———


@router.post("/stream")
async def chat_stream(
    request: Request,
    body: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _ratelimit: None = Depends(check_rate_limit),
):
    """Authenticated SSE chat streaming endpoint.

    Supports conversation_id mode (recommended) and legacy messages mode.
    """
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
    conversation_id = body.conversation_id or ""
    graph = build_graph()

    if body.conversation_id and body.new_message:
        # New mode: load history from DB, append new message
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
        checkpoint_config = {"configurable": {"thread_id": conversation_id}}
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
        if snapshot.values and not checkpoint_synced:
            await request.app.state.checkpointer.adelete_thread(conversation_id)
        input_messages = (
            [new_user_msg]
            if checkpoint_synced
            else [*history, new_user_msg]
        )

        await persist_user_message(db, conversation_id, new_user_msg)

    elif body.messages:
        # Legacy mode: full history from client
        input_messages = _convert_messages(body.messages)
    else:
        return Response(
            content=json.dumps({"error": "缺少对话内容"}),
            status_code=400,
            media_type="application/json",
        )

    # SSE streaming setup
    event_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def send_event(event: dict[str, Any]) -> None:
        await event_queue.put(event)

    checkpointer = getattr(request.app.state, "checkpointer", None)
    tool_cache = getattr(request.app.state, "tool_cache", None)

    async def invalidate_checkpoint() -> None:
        if checkpointer is not None and conversation_id:
            try:
                await checkpointer.adelete_thread(conversation_id)
            except Exception:
                logger.exception("Failed to invalidate checkpoint for %s", conversation_id)

    async def run_and_persist() -> None:
        try:
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
                body.search_mode,
            )
            if conversation_id and new_messages:
                async with async_session() as persistence_db:
                    await persist_graph_messages(
                        persistence_db,
                        conversation_id,
                        new_messages,
                    )
            done_id = next(
                (str(message.id) for message in reversed(new_messages) if message.id),
                "",
            )
            await send_event({"type": "done", "messageId": done_id})
        except asyncio.CancelledError:
            logger.info("Graph run cancelled for conversation %s", conversation_id)
            await invalidate_checkpoint()
            raise
        except Exception as exc:
            logger.exception("Graph or persistence failure")
            await invalidate_checkpoint()
            await send_event({
                "type": "error",
                "message": f"生成或保存回复时发生错误: {exc}",
                "code": "STREAM_ERROR",
            })

    graph_task = asyncio.create_task(run_and_persist())

    return StreamingResponse(
        stream_sse_events(event_queue, graph_task),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
