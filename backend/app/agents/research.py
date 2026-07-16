"""Research Agent dedicated to fast web search and bounded deep research."""

import asyncio
import json
import logging
import uuid
from typing import Any, cast

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.runtime import Runtime

from app.graph.context import AgentRuntimeContext
from app.graph.model import FORCED_SEARCH_CALL_PREFIX, emit
from app.graph.state import AgentState
from app.graph.tool_execution import execute_tool_batch

logger = logging.getLogger("chatbot.agents.research")


def _research_call(state: AgentState, context: AgentRuntimeContext) -> tuple[AIMessage, str]:
    decision = state.get("supervisor_decision")
    task = decision["task"] if decision else ""
    tool_name = "web_search" if context.search_mode == "web" else "deep_search"
    args: dict[str, Any] = {"query": task}
    if tool_name == "web_search":
        args["max_results"] = 5
    else:
        args["focus"] = decision["reason"] if decision else ""
    call_id = f"{FORCED_SEARCH_CALL_PREFIX}{uuid.uuid4().hex}"
    return (
        AIMessage(
            content="",
            tool_calls=[{
                "id": call_id,
                "name": tool_name,
                "args": args,
                "type": "tool_call",
            }],
            id=uuid.uuid4().hex,
        ),
        call_id,
    )


def _worker_result(tool_message: ToolMessage) -> str:
    try:
        output = json.loads(str(tool_message.content))
    except (json.JSONDecodeError, TypeError):
        return str(tool_message.content)
    if not isinstance(output, dict):
        return str(output)
    if output.get("error"):
        return f"Research Agent 执行失败：{output['error']}"
    summary = str(output.get("summary", "")).strip()
    if summary:
        return summary
    return json.dumps(output, ensure_ascii=False)


async def research_worker_node(
    state: AgentState,
    runtime: Runtime[AgentRuntimeContext],
) -> dict[str, Any]:
    """Execute the Supervisor assignment through the dedicated search path."""
    if not state.get("supervisor_decision"):
        return {"error": "Supervisor 未提供 Research Agent 任务"}
    context = runtime.context
    ai_message, call_id = _research_call(state, context)
    call = ai_message.tool_calls[0]
    args_json = json.dumps(call["args"], ensure_ascii=False, separators=(",", ":"))
    try:
        await emit(context.stream_callback, {
            "type": "activity",
            "kind": "searching",
            "message": "Research Agent 正在执行专用搜索任务",
        })
        await emit(context.stream_callback, {
            "type": "tool_call_start",
            "messageId": str(ai_message.id),
            "toolCallId": call_id,
            "toolName": call["name"],
        })
        await emit(context.stream_callback, {
            "type": "tool_call_delta",
            "toolCallId": call_id,
            "delta": args_json,
        })
        await emit(context.stream_callback, {
            "type": "tool_call_end",
            "toolCallId": call_id,
        })
        tool_state = cast(AgentState, {
            **state,
            "messages": [ai_message],
        })
        result = await execute_tool_batch(tool_state, context)
        tool_messages = result.get("messages", [])
        if not tool_messages or not isinstance(tool_messages[0], ToolMessage):
            return {"error": "Research Agent 未得到有效工具结果"}
        completed = list(state.get("completed_agents", []))
        completed.append("research_agent")
        return {
            "messages": [ai_message, *tool_messages],
            "worker_result": _worker_result(tool_messages[0]),
            "source_citations": result.get("source_citations", []),
            "active_agent": "research_agent",
            "completed_agents": completed,
        }
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Research Agent failed")
        return {"error": str(exc)}
