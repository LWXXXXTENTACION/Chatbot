"""
Specialist agents: domain-specific chat nodes invoked by the RouterAgent.

Each specialist has a tailored system prompt and optional tool restrictions.
The core logic reuses the streaming pattern from nodes.py's chat_node.
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

logger = logging.getLogger("chatbot.specialists")

StreamCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]

# ═══════════════════════════════════════════
#  Specialist System Prompts
# ═══════════════════════════════════════════

CODE_SYSTEM_PROMPT = """你是一个专业的编程助手，擅长代码编写和软件工程。

回答准则：
- 代码使用 ```语言 标记，包含完整可运行的示例
- 标注时间/空间复杂度（如适用）
- 说明关键设计决策和可选的替代方案
- 考虑边界情况和错误处理
- 遵循语言社区的最佳实践和代码风格
- 回答使用中文解说，代码注释使用英文
- **重要**：使用 create_artifact 工具展示完整代码或 HTML/SVG 时，文本回复中**不要**重复输出相同的完整代码。只需用一两句话简要说明创建了什么以及关键设计要点。如果你选择不使用 create_artifact（例如简短的代码片段），直接在文本中使用 ```代码块是完全可以的
- **引用**：使用 web_search 搜索后：
  1. 在引用信息的地方标注文档编号，例如 [1]、[2]
  2. 序号对应搜索结果顺序
  3. 在回答末尾附上参考资料列表（标题 + URL）
  4. 如果搜索结果不足或不相关，请诚实说明并建议用户提供更多信息
- **搜索评估**：收到搜索结果后，先评估：
  1. 结果是否直接相关？如果不相关，请改写搜索词重新搜索
  2. 信息是否足够回答问题？如果不足，用更精准的关键词补充搜索
  3. 同一主题最多搜索 3 次，避免无限循环"""

MATH_SYSTEM_PROMPT = """你是一个专业的数学和计算助手，擅长数学推导和数据分析。

回答准则：
- 逐步推导，每步清晰编号
- 公式使用 LaTeX 格式（$$ 块级，$ 行内）
- 结果进行验证或提供交叉检验
- 涉及计算时主动使用 calculate 工具
- 解释每个步骤的数学原理
- 回答使用中文
- **重要**：使用 create_artifact 展示推导过程或文档时，文本回复中**不要**重复工件的完整内容，只需简要总结。使用 calculate 工具后，文本回复中也不要重复工具已返回的完整结果
- **引用**：使用 web_search 搜索后：
  1. 在引用信息的地方标注文档编号，例如 [1]、[2]
  2. 序号对应搜索结果顺序
  3. 在回答末尾附上参考资料列表（标题 + URL）
  4. 如果搜索结果不足或不相关，请诚实说明并建议用户提供更多信息
- **搜索评估**：收到搜索结果后，先评估：
  1. 结果是否直接相关？如果不相关，请改写搜索词重新搜索
  2. 信息是否足够回答问题？如果不足，用更精准的关键词补充搜索
  3. 同一主题最多搜索 3 次，避免无限循环"""

CREATIVE_SYSTEM_PROMPT = """你是一个专业的写作和创意助手，擅长文案创作和内容策划。

回答准则：
- 根据用户需求调整文风和语气（正式/轻松/专业/文艺）
- 注重结构和逻辑，段落分明
- 中文表达地道流畅，避免翻译腔
- 如有修改建议，提供 before/after 对比
- 主动提供多个版本供用户选择
- 回答使用中文
- **重要**：使用 create_artifact 工具展示完整文案或长文档时，文本回复中**不要**重复相同内容，只需简要介绍创建了什么
- **引用**：使用 web_search 搜索后：
  1. 在引用信息的地方标注文档编号，例如 [1]、[2]
  2. 序号对应搜索结果顺序
  3. 在回答末尾附上参考资料列表（标题 + URL）
  4. 如果搜索结果不足或不相关，请诚实说明并建议用户提供更多信息
- **搜索评估**：收到搜索结果后，先评估：
  1. 结果是否直接相关？如果不相关，请改写搜索词重新搜索
  2. 信息是否足够回答问题？如果不足，用更精准的关键词补充搜索
  3. 同一主题最多搜索 3 次，避免无限循环"""

GENERAL_SYSTEM_PROMPT = """你是一个乐于助人的 AI 助手。

回答准则：
- 简洁准确，用 Markdown 格式化
- 代码标注语言，表格对齐
- 不确定时主动说明
- 回答使用中文
- **重要**：使用 create_artifact 工具时，文本回复中**不要**重复工件的完整内容，只需简要说明
- **引用**：使用 web_search 搜索后：
  1. 在引用信息的地方标注文档编号，例如 [1]、[2]
  2. 序号对应搜索结果顺序
  3. 在回答末尾附上参考资料列表（标题 + URL）
  4. 如果搜索结果不足或不相关，请诚实说明并建议用户提供更多信息
- **搜索评估**：收到搜索结果后，先评估：
  1. 结果是否直接相关？如果不相关，请改写搜索词重新搜索
  2. 信息是否足够回答问题？如果不足，用更精准的关键词补充搜索
  3. 同一主题最多搜索 3 次，避免无限循环"""


# ═══════════════════════════════════════════
#  Specialist System Prompts Map
# ═══════════════════════════════════════════

SPECIALIST_PROMPTS: dict[str, str] = {
    "code": CODE_SYSTEM_PROMPT,
    "math": MATH_SYSTEM_PROMPT,
    "creative": CREATIVE_SYSTEM_PROMPT,
    "general": GENERAL_SYSTEM_PROMPT,
}


# ═══════════════════════════════════════════
#  Tool Restrictions per Specialist
# ═══════════════════════════════════════════

SPECIALIST_TOOLS: dict[str, list] = {
    "code": ALL_TOOLS,           # code agent gets all tools
    "math": ALL_TOOLS,           # math agent especially needs calculate
    "creative": ALL_TOOLS,       # creative can use artifacts
    "general": ALL_TOOLS,        # general gets everything
}


# ═══════════════════════════════════════════
#  Helper
# ═══════════════════════════════════════════

def _get_stream_callback(config: RunnableConfig) -> StreamCallback | None:
    """Extract the stream callback from config.configurable."""
    if config and "configurable" in config:
        return config["configurable"].get("stream_callback")  # type: ignore
    return None


async def _emit(callback: StreamCallback | None, event: dict[str, Any]) -> None:
    """Safely invoke the stream callback."""
    if callback is None:
        return
    try:
        await callback(event)
    except Exception:
        logger.exception("Stream callback failed")


# ═══════════════════════════════════════════
#  Specialist Agent Node Factory
# ═══════════════════════════════════════════

async def _specialist_chat_node(
    state: AgentState,
    config: RunnableConfig,
    category: str,
) -> dict[str, Any]:
    """Core specialist chat node: call LLM with domain prompt + streaming.

    This is the same streaming logic as the original chat_node in nodes.py,
    but uses a category-specific system prompt and tool set.
    """
    callback = _get_stream_callback(config)
    model_id: DeepSeekModelId = state.get("model_id", "deepseek-v4-flash")  # type: ignore
    messages: list[BaseMessage] = state.get("messages", [])
    base_system_prompt = state.get("system_prompt", "")

    # Use category-specific system prompt as primary, fall back to base
    specialist_prompt = SPECIALIST_PROMPTS.get(category, GENERAL_SYSTEM_PROMPT)
    tools_for_category = SPECIALIST_TOOLS.get(category, ALL_TOOLS)

    # Create LLM
    llm = create_deepseek_chat(model_id)

    # Bind tools if supported
    if tools_enabled(model_id):
        llm_with_tools = llm.bind_tools(tools_for_category)
    else:
        llm_with_tools = llm

    # Build message list with specialist prompt
    llm_input: list[BaseMessage] = []
    effective_prompt = specialist_prompt
    if base_system_prompt.strip() and base_system_prompt.strip() != specialist_prompt.strip():
        effective_prompt = f"{specialist_prompt}\n\n额外指示：{base_system_prompt}"

    # Inject supervisor task — the specific sub-task assigned by the
    # supervisor_node. This ensures the specialist focuses on its
    # assigned role rather than trying to handle the entire user request.
    supervisor_task = state.get("supervisor_task", "")
    if supervisor_task:
        effective_prompt = (
            f"{effective_prompt}\n\n"
            f"[当前任务 — 由 Supervisor 分配] {supervisor_task}"
        )

    # When the specialist is being called after a tool execution (the
    # last message is a ToolMessage), inject a hint to prevent it from
    # re-outputting content that was already delivered via the tool.
    # Without this, the specialist often repeats code/artifacts in text
    # that were already shown by the tool result, causing duplicate output.
    if messages and isinstance(messages[-1], ToolMessage):
        effective_prompt = (
            f"{effective_prompt}\n\n"
            "[工具执行后的上下文] 你刚刚通过工具完成了操作，结果已经展示给用户。"
            "现在只需用一两句话简要确认结果，**不要**在文本回复中重复工具已经输出的完整代码或内容。"
        )

    # Inject retrieved search documents as context for citation-aware answers
    retrieved_docs = state.get("retrieved_docs", "")
    if retrieved_docs:
        effective_prompt = (
            f"{effective_prompt}\n\n"
            f"[检索到的参考资料 — 请在回答中引用这些文档]\n{retrieved_docs}"
        )
        await _emit(callback, {
            "type": "activity",
            "kind": "analyzing",
            "message": "🤔 正在分析搜索结果...",
        })

    # Enforce search iteration limit
    search_iteration = state.get("search_iteration", 0)
    if search_iteration >= 3:
        effective_prompt = (
            f"{effective_prompt}\n\n"
            "[搜索限制] 已达到最大搜索次数（3次），请基于现有信息回答，不要再次搜索。"
        )
    # Include search history so the LLM can avoid duplicate queries
    search_history = state.get("search_history", [])
    if search_history:
        history_text = "、".join(search_history)
        effective_prompt = (
            f"{effective_prompt}\n\n"
            f"[搜索历史] 已搜索过的关键词：{history_text}。请避免重复搜索相同内容。"
        )

    llm_input.append(SystemMessage(content=effective_prompt))
    llm_input.extend(list(messages))

    # Emit "answering" activity before streaming begins
    await _emit(callback, {
        "type": "activity",
        "kind": "answering",
        "message": "✍️ 正在生成回答...",
    })

    message_id = f"msg_{uuid.uuid4().hex[:12]}"
    full_text = ""
    full_reasoning = ""
    text_started = False
    reasoning_started = False
    tool_calls_map: dict[str, dict[str, Any]] = {}

    try:
        async for chunk in llm_with_tools.astream(llm_input):
            # Text content
            if chunk.content:
                text = ""
                if isinstance(chunk.content, str):
                    text = chunk.content
                elif isinstance(chunk.content, list):
                    for part in chunk.content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text += part.get("text", "")
                if text:
                    if not text_started:
                        await _emit(callback, {"type": "text_start", "messageId": message_id})
                        text_started = True
                    full_text += text
                    await _emit(callback, {"type": "text_delta", "messageId": message_id, "delta": text})

            # Reasoning content (DeepSeek-specific)
            reasoning = getattr(chunk, "reasoning_content", None)
            if reasoning:
                if not reasoning_started:
                    await _emit(callback, {"type": "reasoning_start", "messageId": message_id})
                    reasoning_started = True
                full_reasoning += reasoning
                await _emit(callback, {"type": "reasoning_delta", "messageId": message_id, "delta": reasoning})

            # Tool call chunks
            if hasattr(chunk, "tool_call_chunks") and chunk.tool_call_chunks:
                for tc_chunk in chunk.tool_call_chunks:
                    tc = dict(tc_chunk) if not isinstance(tc_chunk, dict) else tc_chunk
                    tc_index = tc.get("index", 0)
                    tc_id = tc.get("id") or ""
                    tc_name = tc.get("name") or ""
                    tc_args = tc.get("args") or ""

                    key = f"idx_{tc_index}"
                    if key not in tool_calls_map:
                        tool_calls_map[key] = {
                            "index": tc_index, "id": tc_id,
                            "name": tc_name, "args_json": "", "started": False,
                        }

                    entry = tool_calls_map[key]
                    if tc_id and not entry["id"]:
                        entry["id"] = tc_id
                    if tc_name and not entry["name"]:
                        entry["name"] = tc_name

                    if not entry["started"] and entry["name"]:
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
            await _emit(callback, {"type": "text_end", "messageId": message_id})
        if reasoning_started:
            await _emit(callback, {"type": "reasoning_end", "messageId": message_id})

        for key, entry in tool_calls_map.items():
            call_id = entry["id"] or f"call_{key}"
            await _emit(callback, {"type": "tool_call_end", "toolCallId": call_id})

        # Build final AIMessage
        tool_calls: list[dict[str, Any]] = []
        for key, entry in tool_calls_map.items():
            call_id = entry["id"] or f"call_{key}"
            try:
                args = json.loads(entry["args_json"])
            except (json.JSONDecodeError, KeyError):
                args = {}
            tool_calls.append({
                "id": call_id, "name": entry["name"] or "unknown",
                "args": args, "type": "tool_call",
            })

        final_ai_message = AIMessage(content=full_text or "", additional_kwargs={})
        if tool_calls:
            final_ai_message.tool_calls = tool_calls  # type: ignore
            if full_reasoning:
                final_ai_message.additional_kwargs["reasoning_content"] = full_reasoning

        # Clear retrieved_docs after consumption to prevent repeated
        # injection on subsequent specialist invocations.
        return {"messages": [final_ai_message], "retrieved_docs": ""}

    except asyncio.CancelledError:
        logger.info(f"specialist {category} cancelled")
        if full_text or tool_calls_map:
            return {"messages": [AIMessage(content=full_text or "")]}
        raise
    except Exception as e:
        logger.exception(f"specialist {category} error")
        return {"error": str(e), "messages": []}


# ═══════════════════════════════════════════
#  Expert Tool Node (shared across specialists)
# ═══════════════════════════════════════════

async def specialist_tool_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Shared tool execution node for all specialist agents.

    Wraps LangGraph's built-in ToolNode and emits tool_result events.
    When web_search is executed, extracts results into source_citations
    and retrieved_docs for context injection in the next specialist turn.
    """
    callback = _get_stream_callback(config)
    messages: list[BaseMessage] = state.get("messages", [])

    if not messages:
        return {"messages": []}

    last_message = messages[-1]
    if not isinstance(last_message, AIMessage) or not last_message.tool_calls:
        return {"messages": []}

    # Emit "searching" (or "rewriting" for subsequent searches) activity
    search_iteration = state.get("search_iteration", 0)
    for tc in last_message.tool_calls:
        if tc.get("name") == "web_search":
            query = tc.get("args", {}).get("query", "未知查询")
            if search_iteration > 0:
                await _emit(callback, {
                    "type": "activity",
                    "kind": "rewriting",
                    "message": f"🔄 信息不足，改写搜索词：{query}",
                })
            await _emit(callback, {
                "type": "activity",
                "kind": "searching",
                "message": f"🔍 正在搜索：{query}",
            })

    tool_node = ToolNode(ALL_TOOLS)
    result = await tool_node.ainvoke({"messages": [last_message]}, config)

    tool_messages = result.get("messages", [])
    for msg in tool_messages:
        if isinstance(msg, ToolMessage):
            tool_result = msg.content
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

    # Extract search results for citation context injection
    new_citations: list[dict[str, str]] = []
    new_docs: list[str] = []

    for tc in (last_message.tool_calls or []):
        if tc.get("name") == "web_search":
            query = tc.get("args", {}).get("query", "")
            # Find the matching ToolMessage by tool_call_id
            for msg in tool_messages:
                if isinstance(msg, ToolMessage) and msg.tool_call_id == tc.get("id"):
                    try:
                        parsed = json.loads(msg.content) if isinstance(msg.content, str) else msg.content
                    except (json.JSONDecodeError, TypeError):
                        continue
                    results = parsed.get("results", []) if isinstance(parsed, dict) else []
                    await _emit(callback, {
                        "type": "activity",
                        "kind": "retrieved",
                        "message": f"📄 检索到 {len(results)} 条结果",
                    })
                    for i, r in enumerate(results, 1):
                        title = r.get("title", "")
                        url = r.get("url", "")
                        content = r.get("content", "")
                        new_citations.append({
                            "title": title,
                            "url": url,
                            "content": content,
                        })
                        new_docs.append(
                            f"【文档{i}】\n标题: {title}\nURL: {url}\n内容: {content}\n"
                        )

    update: dict[str, Any] = {}

    # Track search iteration and history
    search_iteration = state.get("search_iteration", 0)
    search_history: list[str] = list(state.get("search_history", []))

    if new_citations:
        # Merge with existing citations from prior search rounds
        existing = state.get("source_citations", [])
        update["source_citations"] = existing + new_citations
        # Accumulate retrieved_docs across multiple web_search calls
        existing_docs = state.get("retrieved_docs", "")
        new_docs_text = "\n".join(new_docs)
        update["retrieved_docs"] = (
            existing_docs + "\n" + new_docs_text
            if existing_docs
            else new_docs_text
        )
        # Increment search iteration and track queries
        for tc in (last_message.tool_calls or []):
            if tc.get("name") == "web_search":
                query = tc.get("args", {}).get("query", "")
                if query:
                    search_history.append(query)
        search_iteration += 1

    update["search_iteration"] = search_iteration
    update["search_history"] = search_history

    return {**result, **update}


# ═══════════════════════════════════════════
#  Specialist Node Definitions
# ═══════════════════════════════════════════

async def code_agent(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Code specialist: programming, algorithms, debugging, architecture."""
    return await _specialist_chat_node(state, config, "code")


async def math_agent(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Math specialist: calculation, formula, data analysis."""
    return await _specialist_chat_node(state, config, "math")


async def creative_agent(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Creative specialist: writing, translation, polishing."""
    return await _specialist_chat_node(state, config, "creative")


async def general_agent(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """General specialist: knowledge Q&A, chitchat (catch-all)."""
    return await _specialist_chat_node(state, config, "general")


# ═══════════════════════════════════════════
#  Tool routing per specialist
# ═══════════════════════════════════════════

def specialist_should_continue(state: AgentState) -> str:
    """Check if the last message (from any specialist) has tool calls."""
    messages = state.get("messages", [])
    if not messages:
        return "__end__"

    last_message = messages[-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "tools"
    return "__end__"
