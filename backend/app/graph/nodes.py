"""Single main-agent node and its shared tool dispatcher."""

import asyncio
import json
import logging
import uuid
from typing import Any, Callable, Coroutine

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from app.config import DeepSeekModelId, tools_enabled
from app.graph.deep_search import deep_search_agent
from app.graph.state import AgentState
from app.llm.client import create_deepseek_chat
from app.tools import MAIN_AGENT_TOOLS, STANDARD_TOOLS

logger = logging.getLogger("chatbot.graph")

StreamCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]
STANDARD_TOOL_MAP = {tool.name: tool for tool in STANDARD_TOOLS}

MAIN_AGENT_SYSTEM_PROMPT = """你是唯一直接与用户对话的主 Agent。你负责理解请求、选择工具并给出最终答案；不要把任务路由给代码、数学、创意或通用子 Agent。

工具规则：
- 天气、计算和工件由你直接调用对应工具。
- 只有当问题依赖实时信息、外部事实或明确要求查证时，才调用 deep_search。
- deep_search 会委派给一个独立搜索 Agent；每个用户回合最多调用一次。不要调用或假装调用 raw web_search。
- 能直接回答时不要调用工具。使用 create_artifact 后，不要在正文重复完整工件内容。

回答规则：
- 默认使用中文，简洁、准确，并按需使用 Markdown、代码块或 LaTeX。
- deep_search 返回的 results 是编号来源，summary 是研究简报。重要的可核验结论后立刻添加 [[cite:1]] 或 [[cite:1,2]]。
- 引用编号只能对应本回合 deep_search 的 results；不要编造编号，不要把引用集中到段尾或单独列参考资料。
- 来源不足、冲突或不确定时要明确说明。"""


def _get_stream_callback(config: RunnableConfig) -> StreamCallback | None:
    if config and "configurable" in config:
        return config["configurable"].get("stream_callback")  # type: ignore[return-value]
    return None


async def _emit(callback: StreamCallback | None, event: dict[str, Any]) -> None:
    if callback is None:
        return
    try:
        await callback(event)
    except Exception:
        logger.exception("Stream callback failed")


def _json_content(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return json.dumps({"result": str(value)}, ensure_ascii=False)


async def chat_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Stream one main-agent LLM call and return its assembled AI message."""
    callback = _get_stream_callback(config)
    model_id: DeepSeekModelId = state.get("model_id", "deepseek-v4-flash")  # type: ignore[assignment]
    messages: list[BaseMessage] = state.get("messages", [])
    citations = state.get("source_citations", [])

    llm = create_deepseek_chat(model_id)
    llm_with_tools = llm.bind_tools(MAIN_AGENT_TOOLS) if tools_enabled(model_id) else llm

    system_messages = [SystemMessage(content=MAIN_AGENT_SYSTEM_PROMPT)]
    custom_prompt = state.get("system_prompt", "").strip()
    if custom_prompt:
        system_messages.append(SystemMessage(content=custom_prompt))
    if not tools_enabled(model_id):
        system_messages.append(SystemMessage(
            content="当前模型不支持工具调用。不要声称完成了搜索、计算或其他工具操作。"
        ))
    llm_input: list[BaseMessage] = [*system_messages, *messages]

    message_id = f"msg_{uuid.uuid4().hex[:12]}"
    full_text = ""
    full_reasoning = ""
    text_started = False
    reasoning_started = False
    tool_calls_map: dict[str, dict[str, Any]] = {}

    try:
        async for chunk in llm_with_tools.astream(llm_input):
            content = chunk.content
            text = ""
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = "".join(
                    str(part.get("text", ""))
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            if text:
                if not text_started:
                    await _emit(callback, {"type": "text_start", "messageId": message_id})
                    text_started = True
                full_text += text
                await _emit(callback, {
                    "type": "text_delta",
                    "messageId": message_id,
                    "delta": text,
                })

            reasoning = getattr(chunk, "reasoning_content", None)
            if reasoning:
                if not reasoning_started:
                    await _emit(callback, {"type": "reasoning_start", "messageId": message_id})
                    reasoning_started = True
                full_reasoning += reasoning
                await _emit(callback, {
                    "type": "reasoning_delta",
                    "messageId": message_id,
                    "delta": reasoning,
                })

            for raw_call in getattr(chunk, "tool_call_chunks", None) or []:
                call = dict(raw_call) if not isinstance(raw_call, dict) else raw_call
                index = call.get("index", 0)
                key = f"idx_{index}"
                entry = tool_calls_map.setdefault(key, {
                    "index": index,
                    "id": "",
                    "name": "",
                    "args_json": "",
                    "started": False,
                })
                if call.get("id") and not entry["id"]:
                    entry["id"] = call["id"]
                if call.get("name") and not entry["name"]:
                    entry["name"] = call["name"]
                if not entry["started"] and entry["name"]:
                    entry["started"] = True
                    await _emit(callback, {
                        "type": "tool_call_start",
                        "messageId": message_id,
                        "toolCallId": entry["id"] or f"call_{key}",
                        "toolName": entry["name"],
                    })
                args_delta = call.get("args") or ""
                if args_delta:
                    entry["args_json"] += args_delta
                    await _emit(callback, {
                        "type": "tool_call_delta",
                        "toolCallId": entry["id"] or f"call_{key}",
                        "delta": args_delta,
                    })

        if text_started:
            await _emit(callback, {"type": "text_end", "messageId": message_id})
        if reasoning_started:
            await _emit(callback, {"type": "reasoning_end", "messageId": message_id})

        tool_calls: list[dict[str, Any]] = []
        for key, entry in tool_calls_map.items():
            call_id = entry["id"] or f"call_{key}"
            await _emit(callback, {"type": "tool_call_end", "toolCallId": call_id})
            try:
                args = json.loads(entry["args_json"] or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append({
                "id": call_id,
                "name": entry["name"] or "unknown",
                "args": args,
                "type": "tool_call",
            })

        additional_kwargs: dict[str, Any] = {}
        if full_reasoning:
            additional_kwargs["reasoning_content"] = full_reasoning
        # Sources belong to the final answer, not the preceding tool-call message.
        if full_text and not tool_calls and citations:
            additional_kwargs["sources"] = citations
            await _emit(callback, {
                "type": "sources",
                "messageId": message_id,
                "sources": citations,
            })

        final_message = AIMessage(content=full_text, additional_kwargs=additional_kwargs)
        if tool_calls:
            final_message.tool_calls = tool_calls  # type: ignore[assignment]
        return {"messages": [final_message]}
    except asyncio.CancelledError:
        logger.info("Main-agent call cancelled")
        if full_text:
            return {"messages": [AIMessage(content=full_text)]}
        raise
    except Exception as exc:
        logger.exception("Main-agent call failed")
        return {"error": str(exc), "messages": []}


async def _execute_tool_call(
    call: dict[str, Any],
    *,
    allow_deep_search: bool,
    model_id: DeepSeekModelId,
    callback: StreamCallback | None,
) -> tuple[ToolMessage, list[dict[str, Any]], str, list[str]]:
    """Execute one main-agent tool call and return state additions."""
    call_id = str(call.get("id", ""))
    name = str(call.get("name", ""))
    args = call.get("args", {}) if isinstance(call.get("args", {}), dict) else {}
    citations: list[dict[str, Any]] = []
    research_brief = ""
    queries: list[str] = []
    status = "success"

    try:
        if name == "deep_search":
            if not allow_deep_search:
                status = "error"
                output: Any = {
                    "error": "每个用户回合最多运行一次 deep_search；请使用已有研究结果回答。"
                }
            else:
                output = await deep_search_agent(
                    query=str(args.get("query", "")).strip(),
                    focus=str(args.get("focus", "")).strip(),
                    model_id=model_id,
                    callback=callback,
                )
                citations = output.get("results", [])
                research_brief = str(output.get("summary", ""))
                queries = [str(item) for item in output.get("queries", [])]
        elif name in STANDARD_TOOL_MAP:
            output = await STANDARD_TOOL_MAP[name].ainvoke(args)
        else:
            status = "error"
            output = {"error": f"未知工具：{name}"}
    except Exception as exc:
        logger.exception("Tool %s failed", name)
        status = "error"
        output = {"error": str(exc)}

    await _emit(callback, {
        "type": "tool_result",
        "toolCallId": call_id,
        "result": output,
        "error": output.get("error") if status == "error" and isinstance(output, dict) else None,
    })
    return (
        ToolMessage(
            content=_json_content(output),
            tool_call_id=call_id,
            name=name,
            status=status,  # type: ignore[arg-type]
        ),
        citations,
        research_brief,
        queries,
    )


async def custom_tool_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Execute direct tools and delegate the single allowed research call."""
    callback = _get_stream_callback(config)
    messages = state.get("messages", [])
    if not messages or not isinstance(messages[-1], AIMessage) or not messages[-1].tool_calls:
        return {"messages": []}

    calls = messages[-1].tool_calls
    already_searched = state.get("search_iteration", 0) > 0
    first_deep_index = next(
        (index for index, call in enumerate(calls) if call.get("name") == "deep_search"),
        None,
    )
    model_id: DeepSeekModelId = state.get("model_id", "deepseek-v4-flash")  # type: ignore[assignment]
    results = await asyncio.gather(*(
        _execute_tool_call(
            call,
            allow_deep_search=(
                not already_searched and first_deep_index is not None and index == first_deep_index
            ),
            model_id=model_id,
            callback=callback,
        )
        for index, call in enumerate(calls)
    ))

    tool_messages = [item[0] for item in results]
    new_citations = next((item[1] for item in results if item[1]), [])
    research_brief = next((item[2] for item in results if item[2]), "")
    new_queries = [query for item in results for query in item[3]]
    deep_search_attempted = any(call.get("name") == "deep_search" for call in calls)
    return {
        "messages": tool_messages,
        "source_citations": new_citations or state.get("source_citations", []),
        "retrieved_docs": research_brief or state.get("retrieved_docs", ""),
        "search_iteration": state.get("search_iteration", 0) + (1 if deep_search_attempted else 0),
        "search_history": [*state.get("search_history", []), *new_queries],
    }
