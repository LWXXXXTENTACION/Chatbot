"""Execute the workflow's single bounded batch of tool calls."""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import StreamWriter

from app.config import DeepSeekModelId
from app.graph.context import AgentRuntimeContext
from app.graph.deep_search import dedupe_sources, run_deep_search_workflow
from app.graph.model import emit
from app.graph.state import AgentState, SourceCitation
from app.tools import STANDARD_TOOLS, web_search

logger = logging.getLogger("chatbot.graph.tools")

SEARCH_TOOL_NAMES = {"web_search", "deep_search"}
TOOL_MAP = {tool.name: tool for tool in [*STANDARD_TOOLS, web_search]}


def _json_content(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return json.dumps({"result": str(value)}, ensure_ascii=False)


async def _execute_one(
    call: dict[str, Any],
    *,
    allow_search: bool,
    model_id: DeepSeekModelId,
    writer: StreamWriter,
    runtime_context: AgentRuntimeContext,
) -> tuple[ToolMessage, list[SourceCitation]]:
    """Execute one call and return its message plus normalized citations."""
    call_id = str(call.get("id", ""))
    name = str(call.get("name", ""))
    args = call.get("args", {}) if isinstance(call.get("args", {}), dict) else {}
    status = "success"
    citations: list[SourceCitation] = []
    cached = False

    try:
        if name in SEARCH_TOOL_NAMES and not allow_search:
            status = "error"
            output: Any = {
                "error": "一个工作流回合只允许一个搜索任务；请合并搜索范围。"
            }
        else:
            cache = runtime_context.tool_cache
            lookup = await cache.get(name, args, model_id=model_id) if cache else None
            if lookup and lookup.hit:
                output = lookup.value
                cached = True
            elif name == "deep_search":
                output = await run_deep_search_workflow(
                    query=str(args.get("query", "")).strip(),
                    focus=str(args.get("focus", "")).strip(),
                    model_id=model_id,
                    writer=writer,
                )
            elif name in TOOL_MAP:
                output = await TOOL_MAP[name].ainvoke(args)
            else:
                status = "error"
                output = {"error": f"未知工具：{name}"}

            if name == "web_search" and isinstance(output, dict) and not output.get("error"):
                output = {**output, "results": dedupe_sources([output])}
            if not cached and status == "success" and cache:
                await cache.put(name, args, output, model_id=model_id)

        if name in SEARCH_TOOL_NAMES and isinstance(output, dict) and not output.get("error"):
            citations = output.get("results", [])
    except Exception as exc:
        logger.exception("Tool %s failed", name)
        status = "error"
        output = {"error": str(exc)}

    await emit(writer, {
        "type": "tool_result",
        "toolCallId": call_id,
        "result": output,
        "cached": cached,
        "error": output.get("error") if status == "error" and isinstance(output, dict) else None,
    })
    return (
        ToolMessage(
            content=_json_content(output),
            tool_call_id=call_id,
            name=name,
            status=status,  # type: ignore[arg-type]
            additional_kwargs={
                "context_created_at": datetime.now(timezone.utc).isoformat(),
            },
            id=uuid.uuid4().hex,
        ),
        citations,
    )


async def execute_tool_batch(
    state: AgentState,
    context: AgentRuntimeContext,
    writer: StreamWriter,
) -> dict[str, Any]:
    """Execute exactly one tool batch selected by the decision node."""
    messages = state.get("messages", [])
    if not messages or not isinstance(messages[-1], AIMessage):
        return {"error": "工具阶段缺少 Agent 决策消息"}
    calls = messages[-1].tool_calls
    if not calls:
        return {"error": "工具阶段没有可执行的工具调用"}

    first_search_index = next(
        (index for index, call in enumerate(calls) if call.get("name") in SEARCH_TOOL_NAMES),
        None,
    )
    model_id: DeepSeekModelId = state.get("model_id", "deepseek-v4-flash")  # type: ignore[assignment]
    results = await asyncio.gather(*(
        _execute_one(
            call,
            allow_search=first_search_index is None or index == first_search_index,
            model_id=model_id,
            writer=writer,
            runtime_context=context,
        )
        for index, call in enumerate(calls)
    ))
    citations = next((item[1] for item in results if item[1]), [])
    return {
        "messages": [item[0] for item in results],
        "source_citations": citations,
    }


async def execute_general_tool_batch(
    calls: list[dict[str, Any]],
    *,
    model_id: DeepSeekModelId,
    context: AgentRuntimeContext,
    writer: StreamWriter,
) -> list[ToolMessage]:
    """Execute a General Agent batch while rejecting every search tool."""
    results = await asyncio.gather(*(
        _execute_one(
            call,
            allow_search=False,
            model_id=model_id,
            writer=writer,
            runtime_context=context,
        )
        for call in calls
    ))
    return [item[0] for item in results]
