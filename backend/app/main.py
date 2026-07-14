"""
FastAPI application entry point.
Exposes /chat/stream (SSE) and /ws (WebSocket, kept for direct testing).
"""

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import StreamingResponse, Response
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from app.config import (
    DEEPSEEK_API_KEY,
    DEFAULT_MODEL,
    DEFAULT_SYSTEM_PROMPT,
    DeepSeekModelId,
    validate_model,
)
from app.database.engine import get_db
from app.database.models import Base
from app.graph.builder import build_graph
from app.graph.state import AgentState

logger = logging.getLogger("chatbot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create database tables on startup."""
    from app.database.engine import engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created")
    yield
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
app.include_router(auth_router)
app.include_router(conversations_router)
app.include_router(chat_router)

# Reused by the unauthenticated compatibility endpoints below.
_graph = build_graph()


def _extract_text(msg: dict[str, Any]) -> str:
    """Extract text from a message dict, checking both content and parts fields."""
    content = msg.get("content", "")
    parts = msg.get("parts", None)

    # If content is non-empty, use it directly
    if content and str(content).strip():
        return str(content)

    # Otherwise extract from parts (the frontend sends text as TextPart)
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


async def _run_graph_and_stream(
    raw_messages: list[dict[str, Any]],
    model_id: DeepSeekModelId,
    system_prompt: str,
    send_event: Any,
) -> None:
    """Run the LangGraph agent and emit events via the callback."""
    message_id = f"msg_{uuid.uuid4().hex[:12]}"
    try:
        lc_messages = _convert_messages(raw_messages)
        initial_state: AgentState = {
            "messages": lc_messages,
            "model_id": model_id,
            "system_prompt": system_prompt,
            "user_id": "",
            "conversation_id": "",
            "source_citations": [],
            "retrieved_docs": "",
            "search_iteration": 0,
            "search_history": [],
            "error": None,
        }
        config = {
            "configurable": {"stream_callback": send_event},
            "recursion_limit": 12,
        }
        await _graph.ainvoke(initial_state, config=config)  # type: ignore[arg-type]
        # Send done event after successful completion
        await send_event({"type": "done", "messageId": message_id})
    except asyncio.CancelledError:
        logger.info("Graph run cancelled")
    except Exception as e:
        logger.exception("Graph stream error")
        await send_event({
            "type": "error",
            "message": f"调用 DeepSeek API 时发生错误: {str(e)}",
            "code": "STREAM_ERROR",
        })


# ============================================================
# SSE endpoint (used by Next.js API route proxy)
# ============================================================

@app.post("/chat/stream")
async def chat_stream(request: Request):
    """SSE streaming endpoint using raw ASGI send for true streaming."""

    # Parse request
    try:
        body = await request.json()
    except Exception:
        return Response(
            content=json.dumps({"error": "无效的 JSON 请求"}),
            status_code=400,
            media_type="application/json",
        )

    messages = body.get("messages", [])
    model_id = validate_model(body.get("model", DEFAULT_MODEL))
    system = body.get("system", DEFAULT_SYSTEM_PROMPT)

    if not DEEPSEEK_API_KEY:
        return Response(
            content=json.dumps({"error": "未配置 DEEPSEEK_API_KEY"}),
            status_code=500,
            media_type="application/json",
        )

    event_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    stop = False

    async def send_event(event: dict[str, Any]) -> None:
        nonlocal stop
        await event_queue.put(event)
        if event.get("type") in ("done", "error"):
            stop = True

    graph_task = asyncio.create_task(
        _run_graph_and_stream(messages, model_id, system, send_event)
    )

    async def sse_generator():
        nonlocal stop
        try:
            while not stop:
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=60.0)
                except asyncio.TimeoutError:
                    yield b": keepalive\n\n"
                    continue
                if event is None:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
        finally:
            if not graph_task.done():
                graph_task.cancel()
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


# ============================================================
# WebSocket endpoint (kept for direct testing / non-proxy envs)
# ============================================================

@app.websocket("/ws")
async def chat_websocket(ws: WebSocket) -> None:
    await ws.accept()
    logger.info("WebSocket connected")

    stop_event = asyncio.Event()
    current_task: asyncio.Task[Any] | None = None

    async def send_event(event: dict[str, Any]) -> None:
        try:
            await ws.send_text(json.dumps(event, ensure_ascii=False))
        except Exception:
            logger.exception("Failed to send WS event")

    async def run_graph_stream(
        raw_messages: list[dict[str, Any]],
        model_id: DeepSeekModelId,
        system_prompt: str,
    ) -> None:
        await _run_graph_and_stream(raw_messages, model_id, system_prompt, send_event)

    try:
        async for raw in ws.iter_text():
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await send_event({"type": "error", "message": "无效的 JSON 消息", "code": "PARSE_ERROR"})
                continue

            msg_type = data.get("type", "")

            if msg_type == "send":
                if current_task and not current_task.done():
                    stop_event.set()
                    current_task.cancel()
                    try:
                        await current_task
                    except asyncio.CancelledError:
                        pass

                stop_event.clear()
                messages = data.get("messages", [])
                model_id = validate_model(data.get("model", DEFAULT_MODEL))
                system = data.get("system", DEFAULT_SYSTEM_PROMPT)

                if not DEEPSEEK_API_KEY:
                    await send_event({"type": "error", "message": "未配置 DEEPSEEK_API_KEY", "code": "CONFIG_ERROR"})
                    continue

                current_task = asyncio.create_task(run_graph_stream(messages, model_id, system))

            elif msg_type == "stop":
                stop_event.set()
                if current_task and not current_task.done():
                    current_task.cancel()

            elif msg_type == "ping":
                await send_event({"type": "pong"})

            else:
                await send_event({"type": "error", "message": f"未知消息类型: {msg_type}", "code": "UNKNOWN_TYPE"})

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
        stop_event.set()
        if current_task and not current_task.done():
            current_task.cancel()
    except Exception:
        logger.exception("WebSocket error")
    finally:
        stop_event.set()
        if current_task and not current_task.done():
            current_task.cancel()
