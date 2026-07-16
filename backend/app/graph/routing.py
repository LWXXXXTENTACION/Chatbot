"""Explicit worker routing for the Supervisor graph."""

from app.graph.state import AgentState, WorkerRoute


def route_supervisor(state: AgentState) -> WorkerRoute:
    """Return the single worker selected by the Supervisor."""
    decision = state.get("supervisor_decision")
    if decision and decision["route"] == "research_agent":
        return "research_agent"
    return "general_agent"
