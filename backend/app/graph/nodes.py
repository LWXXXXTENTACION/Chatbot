"""Shared lifecycle nodes for the Supervisor workflow."""

from typing import Any

from app.graph.state import AgentState


def prepare_turn_node(_state: AgentState) -> dict[str, Any]:
    """Reset every turn-local coordination field after checkpoint restore."""
    return {
        "supervisor_decision": None,
        "active_agent": None,
        "completed_agents": [],
        "worker_result": "",
        "source_citations": [],
        "context_report": None,
        "error": None,
    }
