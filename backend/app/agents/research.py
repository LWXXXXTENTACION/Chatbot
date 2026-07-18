"""Research Agent 子图：计划搜索、构造调用、执行工具、汇总证据。"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Literal

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import StreamWriter

from app.graph.context import AgentRuntimeContext
from app.graph.events import emit_activity, emit_tool_call
from app.graph.model import FORCED_SEARCH_CALL_PREFIX
from app.graph.state import AgentState, ResearchPlan
from app.graph.tool_execution import execute_tool_batch

logger = logging.getLogger("chatbot.agents.research")


async def prepare_research_node(
    state: AgentState,
    runtime: Runtime[AgentRuntimeContext],
    writer: StreamWriter,
) -> dict[str, Any]:
    """节点 1：把分派任务和搜索模式固化为可审计计划。"""
    decision = state.get("supervisor_decision")
    if not decision:
        return {"error": "Supervisor 未提供 Research Agent 任务"}

    tool_name: Literal["web_search", "deep_search"] = (
        "web_search" if runtime.context.search_mode == "web" else "deep_search"
    )
    plan: ResearchPlan = {
        "tool_name": tool_name,
        "query": decision["task"],
        "focus": decision["reason"] if tool_name == "deep_search" else "",
        "max_results": 5 if tool_name == "web_search" else None,
    }
    await emit_activity(
        writer,
        kind="searching",
        message=f"Research Agent 已规划 {tool_name} 搜索任务",
    )
    return {
        "active_agent": "research_agent",
        "research_plan": plan,
    }


def route_research_plan(state: AgentState) -> Literal["build_call", "complete"]:
    """边：计划无效时不再进入工具节点。"""
    return "complete" if state.get("error") else "build_call"


async def build_research_call_node(
    state: AgentState,
    writer: StreamWriter,
) -> dict[str, Any]:
    """节点 2：把搜索计划转换成标准 AIMessage.tool_calls。"""
    plan = state.get("research_plan")
    if not plan:
        return {"error": "Research Agent 缺少搜索计划"}
    args: dict[str, Any] = {"query": plan["query"]}
    if plan["tool_name"] == "web_search":
        args["max_results"] = plan["max_results"] or 5
    else:
        args["focus"] = plan["focus"]
    message = AIMessage(
        content="",
        id=uuid.uuid4().hex,
        tool_calls=[{
            "id": f"{FORCED_SEARCH_CALL_PREFIX}{uuid.uuid4().hex}",
            "name": plan["tool_name"],
            "args": args,
            "type": "tool_call",
        }],
    )
    await emit_tool_call(writer, message)
    return {"messages": [message]}


async def research_tools_node(
    state: AgentState,
    runtime: Runtime[AgentRuntimeContext],
    writer: StreamWriter,
) -> dict[str, Any]:
    """节点 3：通过统一策略链执行搜索工具并写回来源。"""
    if state.get("error"):
        return {}
    try:
        result = await execute_tool_batch(state, runtime.context, writer)
        return {
            "messages": result.get("messages", []),
            "source_citations": result.get("source_citations", []),
            "error": result.get("error"),
        }
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Research Agent tool node failed")
        return {"error": str(exc)}


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
    return summary or json.dumps(output, ensure_ascii=False)


def finalize_research_node(state: AgentState) -> dict[str, Any]:
    """节点 4：从 ToolMessage 提取简报，并登记 Research Agent 完成。"""
    messages = state.get("messages", [])
    last = messages[-1] if messages else None
    completed = list(state.get("completed_agents", []))
    if state.get("error"):
        return {"active_agent": "research_agent", "completed_agents": completed}
    if not isinstance(last, ToolMessage):
        return {
            "error": "Research Agent 未得到有效工具结果",
            "active_agent": "research_agent",
            "completed_agents": completed,
        }
    if "research_agent" not in completed:
        completed.append("research_agent")
    return {
        "worker_result": _worker_result(last),
        "active_agent": "research_agent",
        "completed_agents": completed,
    }


def build_research_agent_graph():
    """编译无隐藏分支的 Research 子图。"""
    graph = StateGraph(AgentState, context_schema=AgentRuntimeContext)
    graph.add_node("prepare_research", prepare_research_node)
    graph.add_node("build_research_call", build_research_call_node)
    graph.add_node("research_tools", research_tools_node)
    graph.add_node("finalize_research", finalize_research_node)

    graph.add_edge(START, "prepare_research")
    graph.add_conditional_edges(
        "prepare_research",
        route_research_plan,
        {"build_call": "build_research_call", "complete": "finalize_research"},
    )
    graph.add_edge("build_research_call", "research_tools")
    graph.add_edge("research_tools", "finalize_research")
    graph.add_edge("finalize_research", END)

    # 不创建独立 saver；默认作用域会继承父图当前 invocation 的 checkpoint。
    return graph.compile(name="research_agent_workflow")


RESEARCH_AGENT_GRAPH = build_research_agent_graph()
