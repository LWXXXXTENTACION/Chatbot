"""Build the Supervisor multi-agent LangGraph workflow."""

from langgraph.graph import END, START, StateGraph

from app.agents.general import general_worker_node
from app.agents.research import research_worker_node
from app.agents.supervisor import supervisor_finalize_node, supervisor_node
from app.graph.context import AgentRuntimeContext
from app.graph.nodes import prepare_turn_node
from app.graph.routing import route_supervisor
from app.graph.state import AgentInput, AgentOutput, AgentState


def build_graph(*, checkpointer=None):
    """Compile one Supervisor assignment and integration cycle.

    START → prepare_turn → supervisor → one worker → supervisor_finalize → END
    """
    graph = StateGraph(
        AgentState,
        context_schema=AgentRuntimeContext,
        input_schema=AgentInput,
        output_schema=AgentOutput,
    )
    graph.add_node("prepare_turn", prepare_turn_node)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("general_agent", general_worker_node)
    graph.add_node("research_agent", research_worker_node)
    graph.add_node("supervisor_finalize", supervisor_finalize_node)

    graph.add_edge(START, "prepare_turn")
    graph.add_edge("prepare_turn", "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route_supervisor,
        {
            "general_agent": "general_agent",
            "research_agent": "research_agent",
        },
    )
    graph.add_edge("general_agent", "supervisor_finalize")
    graph.add_edge("research_agent", "supervisor_finalize")
    graph.add_edge("supervisor_finalize", END)
    return graph.compile(checkpointer=checkpointer, name="supervisor_chat_workflow")
