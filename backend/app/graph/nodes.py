"""
LangGraph nodes: chat_node (LLM call with streaming) and tool_node (execution).
"""

import asyncio
import json
import logging
import uuid
from typing import Any, Callable, Coroutine

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import ToolNode

from app.config import DeepSeekModelId, tools_enabled
from app.llm.client import create_deepseek_chat
from app.graph.state import AgentState
from app.tools import ALL_TOOLS

logger = logging.getLogger("chatbot.graph")

# Type for the stream callback: async function that receives a dict event
StreamCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


def _get_stream_callback(config: RunnableConfig) -> StreamCallback | None:
    """Extract the stream callback from config.configurable."""
    if config and "configurable" in config:
        return config["configurable"].get("stream_callback")  # type: ignore
    return None


async def chat_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Chat node: calls the LLM with streaming, emits events via StreamCallback.

    Returns a dict with the assembled AIMessage to be merged into state.messages
    via the add_messages reducer.
    """
    callback = _get_stream_callback(config)
    model_id: DeepSeekModelId = state.get("model_id", "deepseek-v4-flash")  # type: ignore
    system_prompt = state.get("system_prompt", "")
    messages: list[BaseMessage] = state.get("messages", [])  # type: ignore

    # Create LLM instance
    llm = create_deepseek_chat(model_id)

    # Bind tools if enabled for this model
    if tools_enabled(model_id):
        llm_with_tools = llm.bind_tools(ALL_TOOLS)
    else:
        llm_with_tools = llm

    # Build the message list: prepend system message if present
    llm_input: list[BaseMessage] = []
    if system_prompt:
        llm_input.append(SystemMessage(content=system_prompt))
    llm_input.extend(list(messages))

    message_id = f"msg_{uuid.uuid4().hex[:12]}"
    full_text = ""
    full_reasoning = ""
    text_started = False
    reasoning_started = False
    tool_calls_map: dict[str, dict[str, Any]] = {}
    final_ai_message: AIMessage | None = None

    try:
        async for chunk in llm_with_tools.astream(llm_input):
            # Handle text content
            if chunk.content:
                text = ""
                if isinstance(chunk.content, str):
                    text = chunk.content
                elif isinstance(chunk.content, list):
                    # Multi-modal content — extract text
                    for part in chunk.content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text += part.get("text", "")
                if text:
                    if not text_started:
                        await _emit(callback, {
                            "type": "text_start",
                            "messageId": message_id,
                        })
                        text_started = True
                    full_text += text
                    await _emit(callback, {
                        "type": "text_delta",
                        "messageId": message_id,
                        "delta": text,
                    })

            # Handle reasoning content (DeepSeek-specific)
            reasoning = getattr(chunk, "reasoning_content", None)
            if reasoning:
                if not reasoning_started:
                    await _emit(callback, {
                        "type": "reasoning_start",
                        "messageId": message_id,
                    })
                    reasoning_started = True
                full_reasoning += reasoning
                await _emit(callback, {
                    "type": "reasoning_delta",
                    "messageId": message_id,
                    "delta": reasoning,
                })

            # Handle tool call chunks (streaming tool call arguments)
            # OpenAI streams tool calls using `index` for grouping; subsequent
            # chunks for the same call may have id=None and name=None, so we
            # key on `index` and collect id/name from the first chunk that
            # provides them.
            if hasattr(chunk, "tool_call_chunks") and chunk.tool_call_chunks:
                for tc_chunk in chunk.tool_call_chunks:
                    # Normalise to dict (langchain wraps these)
                    tc = dict(tc_chunk) if not isinstance(tc_chunk, dict) else tc_chunk
                    tc_index = tc.get("index", 0)
                    tc_id = tc.get("id") or ""
                    tc_name = tc.get("name") or ""
                    tc_args = tc.get("args") or ""

                    # Use index as stable key across chunks
                    key = f"idx_{tc_index}"
                    if key not in tool_calls_map:
                        tool_calls_map[key] = {
                            "index": tc_index,
                            "id": tc_id,
                            "name": tc_name,
                            "args_json": "",
                            "started": False,
                        }

                    entry = tool_calls_map[key]
                    # Collect id/name from the first chunk that has them
                    if tc_id and not entry["id"]:
                        entry["id"] = tc_id
                    if tc_name and not entry["name"]:
                        entry["name"] = tc_name

                    if not entry["started"] and entry["name"]:
                        # Emit tool_call_start once we know the tool name
                        entry["started"] = True
                        await _emit(callback, {
                            "type": "tool_call_start",
                            "messageId": message_id,
                            "toolCallId": entry["id"] or f"call_{key}",
                            "toolName": entry["name"],
                        })

                    if tc_args:
                        entry["args_json"] += tc_args
                        await _emit(callback, {
                            "type": "tool_call_delta",
                            "toolCallId": entry["id"] or f"call_{key}",
                            "delta": tc_args,
                        })

        # Emit end events
        if text_started:
            await _emit(callback, {
                "type": "text_end",
                "messageId": message_id,
            })
        if reasoning_started:
            await _emit(callback, {
                "type": "reasoning_end",
                "messageId": message_id,
            })

        # Emit tool_call_end for each completed tool call
        for key, entry in tool_calls_map.items():
            call_id = entry["id"] or f"call_{key}"
            await _emit(callback, {
                "type": "tool_call_end",
                "toolCallId": call_id,
            })

        # Build the final AIMessage with proper tool_calls format
        tool_calls: list[dict[str, Any]] = []
        for key, entry in tool_calls_map.items():
            call_id = entry["id"] or f"call_{key}"
            tool_name = entry["name"] or "unknown"
            try:
                args = json.loads(entry["args_json"])
            except (json.JSONDecodeError, KeyError):
                args = {}
            tool_calls.append({
                "id": call_id,
                "name": tool_name,
                "args": args,
                "type": "tool_call",
            })

        final_ai_message = AIMessage(
            content=full_text or "",
            additional_kwargs={},
        )
        if tool_calls:
            final_ai_message.tool_calls = tool_calls  # type: ignore[assignment]
            # Also add reasoning if present
            if full_reasoning:
                final_ai_message.additional_kwargs["reasoning_content"] = full_reasoning

        return {"messages": [final_ai_message]}

    except asyncio.CancelledError:
        logger.info("chat_node cancelled")
        # Return whatever we have so far
        if full_text or tool_calls_map:
            partial_msg = AIMessage(content=full_text or "")
            return {"messages": [partial_msg]}
        raise
    except Exception as e:
        logger.exception("chat_node error")
        return {"error": str(e), "messages": []}


async def custom_tool_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Custom tool execution node that emits tool_result events.

    Wraps LangGraph's built-in ToolNode and adds streaming events for
    tool execution results.
    """
    callback = _get_stream_callback(config)
    messages: list[BaseMessage] = state.get("messages", [])  # type: ignore

    if not messages:
        return {"messages": []}

    last_message = messages[-1]
    if not isinstance(last_message, AIMessage) or not last_message.tool_calls:
        return {"messages": []}

    # Use the built-in ToolNode for execution
    tool_node = ToolNode(ALL_TOOLS)
    result = await tool_node.ainvoke({"messages": [last_message]}, config)

    # Emit tool_result events for each tool call
    tool_messages = result.get("messages", [])
    for msg in tool_messages:
        if isinstance(msg, ToolMessage):
            tool_result = msg.content
            # Try to parse as JSON for better display
            try:
                parsed = json.loads(tool_result) if isinstance(tool_result, str) else tool_result
            except (json.JSONDecodeError, TypeError):
                parsed = tool_result

            await _emit(callback, {
                "type": "tool_result",
                "toolCallId": msg.tool_call_id,
                "result": parsed,
                "error": None if msg.status != "error" else tool_result,
            })

    return result


async def _emit(callback: StreamCallback | None, event: dict[str, Any]) -> None:
    """Safely invoke the stream callback."""
    if callback is None:
        return
    try:
        await callback(event)
    except Exception:
        logger.exception("Stream callback failed")
