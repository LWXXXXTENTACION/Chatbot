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
from app.database.engine import get_db
from app.database.models import Conversation, Message, MessagePart, User
from app.graph.builder import build_graph
from app.graph.state import AgentState
from app.middleware.auth import get_current_user
from app.middleware.rate_limit import check_rate_limit
from app.schemas.chat import ChatRequest

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
                            result.append(ToolMessage(
                                content=json.dumps(part["output"], ensure_ascii=False),
                                tool_call_id=tool_call_id,
                            ))
                ai_msg = AIMessage(content="\n".join(text_parts) if text_parts else "")
                if tool_calls:
                    ai_msg.tool_calls = tool_calls  # type: ignore[assignment]
                result.append(ai_msg)
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


# ——— Database persistence ———


async def _persist_messages(
    db: AsyncSession,
    conversation_id: str,
    new_lc_messages: list[BaseMessage],
) -> None:
    """Persist new assistant/tool messages to the database."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    for lc_msg in new_lc_messages:
        if isinstance(lc_msg, (SystemMessage,)):
            continue  # Don't persist system messages

        role = "user"
        if isinstance(lc_msg, HumanMessage):
            role = "user"
        elif isinstance(lc_msg, AIMessage):
            role = "assistant"
        elif isinstance(lc_msg, ToolMessage):
            role = "tool"
        else:
            continue

        msg = Message(
            id=uuid.uuid4().hex,
            conversation_id=conversation_id,
            role=role,
            created_at=now,
        )
        db.add(msg)

        # Create message parts
        parts: list[MessagePart] = []
        pos = 0

        if isinstance(lc_msg, AIMessage):
            # Text content
            text = lc_msg.content
            if isinstance(text, str) and text.strip():
                parts.append(MessagePart(
                    id=uuid.uuid4().hex,
                    message_id=msg.id,
                    type="text",
                    text=text,
                    position=pos,
                ))
                pos += 1

            # Reasoning
            reasoning = lc_msg.additional_kwargs.get("reasoning_content")
            if reasoning and isinstance(reasoning, str) and reasoning.strip():
                parts.append(MessagePart(
                    id=uuid.uuid4().hex,
                    message_id=msg.id,
                    type="reasoning",
                    text=reasoning,
                    position=pos,
                ))
                pos += 1

            # Bind research sources to the final answer message so inline
            # citations still resolve after a conversation is reloaded.
            sources = lc_msg.additional_kwargs.get("sources")
            if isinstance(sources, list) and sources:
                parts.append(MessagePart(
                    id=uuid.uuid4().hex,
                    message_id=msg.id,
                    type="sources",
                    tool_output={"results": sources},
                    position=pos,
                ))
                pos += 1

            # Tool calls
            if lc_msg.tool_calls:
                for tc in lc_msg.tool_calls:
                    tc_id = tc.get("id", "")
                    tc_name = tc.get("name", "")
                    tc_args = tc.get("args", {})
                    parts.append(MessagePart(
                        id=uuid.uuid4().hex,
                        message_id=msg.id,
                        type=f"tool-{tc_name}",
                        tool_call_id=tc_id,
                        tool_state="input-available",
                        tool_input=tc_args,
                        position=pos,
                    ))
                    pos += 1

        elif isinstance(lc_msg, HumanMessage):
            text = lc_msg.content
            if isinstance(text, str) and text.strip():
                parts.append(MessagePart(
                    id=uuid.uuid4().hex,
                    message_id=msg.id,
                    type="text",
                    text=text,
                    position=pos,
                ))
                pos += 1
            elif isinstance(text, list):
                for item in text:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(MessagePart(
                            id=uuid.uuid4().hex,
                            message_id=msg.id,
                            type="text",
                            text=item.get("text", ""),
                            position=pos,
                        ))
                        pos += 1

        for part in parts:
            db.add(part)

    # Update tool outputs from ToolMessages — for tools like web_search
    # that return structured results (urls, snippets), persist the output
    # on the corresponding AIMessage's tool-call part so it survives reload.
    for lc_msg in new_lc_messages:
        if isinstance(lc_msg, ToolMessage):
            tool_call_id = lc_msg.tool_call_id
            if not tool_call_id:
                continue
            result = await db.execute(
                select(MessagePart).where(
                    MessagePart.tool_call_id == tool_call_id,
                    MessagePart.message.has(
                        Message.conversation_id == conversation_id
                    ),
                )
            )
            part = result.scalar_one_or_none()
            if part:
                try:
                    output = (
                        json.loads(lc_msg.content)
                        if isinstance(lc_msg.content, str)
                        else lc_msg.content
                    )
                except (json.JSONDecodeError, TypeError):
                    output = lc_msg.content
                part.tool_output = output
                part.tool_state = "output-available"
                db.add(part)

    # Update conversation updated_at
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conv = result.scalar_one_or_none()
    if conv:
        conv.updated_at = now

    await db.commit()


# ——— Graph runner ———


async def _run_graph_and_stream(
    lc_messages: list[BaseMessage],
    model_id: DeepSeekModelId,
    system_prompt: str,
    send_event: Any,
    user_id: str,
    conversation_id: str,
) -> list[BaseMessage]:
    """Run the LangGraph agent and emit events via the callback.

    Returns the new messages added by the graph (assistant + tool messages)
    so the caller can persist them to the database.
    """
    graph = build_graph()
    message_id = f"msg_{uuid.uuid4().hex[:12]}"
    initial_msg_count = len(lc_messages)

    try:
        initial_state: AgentState = {
            "messages": lc_messages,
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
        config = {
            "configurable": {
                "stream_callback": send_event,
                "user_id": user_id,
                "conversation_id": conversation_id,
            },
            # One main-agent/tool loop, with at most one deep-search delegation.
            "recursion_limit": 12,
        }
        final_state = await graph.ainvoke(initial_state, config=config)  # type: ignore[arg-type]
        await send_event({"type": "done", "messageId": message_id})

        # Return only the new messages added by the graph
        all_messages: list[BaseMessage] = final_state.get("messages", [])
        return all_messages[initial_msg_count:]
    except asyncio.CancelledError:
        logger.info("Graph run cancelled")
        return []
    except Exception as e:
        logger.exception("Graph stream error")
        await send_event({
            "type": "error",
            "message": f"调用 DeepSeek API 时发生错误: {str(e)}",
            "code": "STREAM_ERROR",
        })
        return []


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

    # Determine messages to send
    lc_messages: list[BaseMessage] = []
    new_user_msg: BaseMessage | None = None

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

        # Convert stored messages to LangChain format
        for msg in conv.messages or []:
            lc_messages.extend(_db_message_to_lc(msg))

        # Add new user message
        nm = body.new_message
        user_text = nm.content
        if not user_text and nm.parts:
            for p in nm.parts:
                if isinstance(p, dict) and p.get("type") == "text":
                    user_text += p.get("text", "")
        new_user_msg = HumanMessage(content=user_text)
        lc_messages.append(new_user_msg)

        # Persist user message
        await _persist_single_message(db, body.conversation_id, new_user_msg)

    elif body.messages:
        # Legacy mode: full history from client
        lc_messages = _convert_messages(body.messages)
    else:
        return Response(
            content=json.dumps({"error": "缺少对话内容"}),
            status_code=400,
            media_type="application/json",
        )

    # SSE streaming setup
    event_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    stop = False

    async def send_event(event: dict[str, Any]) -> None:
        nonlocal stop
        await event_queue.put(event)
        if event.get("type") in ("done", "error"):
            stop = True

    graph_task = asyncio.create_task(
        _run_graph_and_stream(
            lc_messages,
            model_id,
            system,
            send_event,
            str(current_user.id),
            body.conversation_id or "",
        )
    )

    async def sse_generator():
        nonlocal stop
        collected_messages: list[BaseMessage] = []

        try:
            while not stop:
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=60.0)
                except asyncio.TimeoutError:
                    yield b": keepalive\n\n"
                    continue
                except asyncio.CancelledError:
                    # Client disconnected (e.g. tab closed, page navigated away)
                    break
                if event is None:
                    break

                # Collect tool_result events for persistence
                if event.get("type") == "tool_result":
                    collected_messages.append(ToolMessage(
                        content=json.dumps(event.get("result", {}), ensure_ascii=False),
                        tool_call_id=event.get("toolCallId", ""),
                    ))

                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
        finally:
            if not graph_task.done():
                graph_task.cancel()

            # Persist assistant + tool messages after stream completes.
            # Only when the graph finished normally (not cancelled/interrupted).
            if body.conversation_id and graph_task.done() and not graph_task.cancelled():
                try:
                    new_messages = graph_task.result()
                    if new_messages:
                        await _persist_messages(
                            db, body.conversation_id, new_messages
                        )
                        logger.info(
                            "Persisted %d messages for conversation %s",
                            len(new_messages), body.conversation_id,
                        )
                except Exception:
                    logger.exception("Failed to persist messages")

            # Drain remaining events
            while not event_queue.empty():
                event_queue.get_nowait()

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ——— Helpers ———


def _db_message_to_lc(msg: Message) -> list[BaseMessage]:
    """Convert a DB Message (with parts) to LangChain messages."""
    parts = sorted(msg.parts or [], key=lambda p: p.position)
    result: list[BaseMessage] = []

    if msg.role == "user":
        text = ""
        for p in parts:
            if p.type == "text" and p.text:
                text += p.text
        result.append(HumanMessage(content=text))
    elif msg.role == "assistant":
        text = ""
        reasoning = ""

        for p in parts:
            if p.type == "text" and p.text:
                text += p.text
            elif p.type == "reasoning" and p.text:
                reasoning = p.text

        ai_msg = AIMessage(content=text)
        # NOTE: Do NOT attach tool_calls when loading from DB.
        # ToolMessages are not persisted with results, so attaching tool_calls
        # without corresponding ToolMessages creates an invalid conversation
        # state that causes the LLM to return empty responses on follow-up.
        if reasoning:
            ai_msg.additional_kwargs["reasoning_content"] = reasoning
        result.append(ai_msg)
    elif msg.role == "tool":
        for p in parts:
            if p.tool_output:
                result.append(ToolMessage(
                    content=json.dumps(p.tool_output, ensure_ascii=False),
                    tool_call_id=p.tool_call_id or "",
                ))

    return result


async def _persist_single_message(
    db: AsyncSession,
    conversation_id: str,
    lc_msg: BaseMessage,
) -> None:
    """Persist a single LangChain message to the database."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    if isinstance(lc_msg, HumanMessage):
        role = "user"
    elif isinstance(lc_msg, AIMessage):
        role = "assistant"
    elif isinstance(lc_msg, ToolMessage):
        role = "tool"
    else:
        return

    msg = Message(
        id=uuid.uuid4().hex,
        conversation_id=conversation_id,
        role=role,
        created_at=now,
    )
    db.add(msg)

    pos = 0
    if isinstance(lc_msg, HumanMessage):
        text = lc_msg.content
        if isinstance(text, str) and text.strip():
            db.add(MessagePart(
                id=uuid.uuid4().hex,
                message_id=msg.id,
                type="text",
                text=text,
                position=pos,
            ))

    await db.commit()
