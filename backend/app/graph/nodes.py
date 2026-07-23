"""Supervisor 父图共用的回合生命周期节点。"""

from typing import Any

from app.graph.state import AgentState


def prepare_turn_node(_state: AgentState) -> dict[str, Any]:
    """重置当前回合字段，防止 checkpoint 恢复后沿用上轮子图进度。"""
    return {
        "supervisor_decision": None,
        "active_agent": None,
        "completed_agents": [],
        "worker_result": "",
        "source_citations": [],
        "context_report": None,
        "retrieved_context": [],
        "context_archive_queue": [],
        "general_task_route": None,
        "tool_rounds": 0,
        "artifact_plan": None,
        "artifact_content": "",
        "research_plan": None,
        "error": None,
    }
