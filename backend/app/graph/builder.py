"""
Graph builder: assembles the LangGraph StateGraph with SupervisorAgent.

True Supervisor-Worker pattern (iterative):
    START → supervisor_node → [supervisor_route]
      → code_agent / math_agent / creative_agent / general_agent
      → [specialist_should_continue]
        → specialist_tool_node → (loop back to specialist)
        → supervisor_node  ← agents report back to supervisor
      → supervisor_node → [supervisor_route]
        → another agent (if needed) or END (if finished)

The supervisor can dispatch multiple agents in sequence for complex tasks.
Each agent's output is evaluated before deciding the next step or finishing.
"""

from langgraph.graph import END, START, StateGraph

from app.graph.state import AgentState
from app.graph.supervisor import supervisor_node, supervisor_route
from app.graph.specialists import (
    code_agent,
    math_agent,
    creative_agent,
    general_agent,
    specialist_tool_node,
    specialist_should_continue,
)


def build_graph() -> StateGraph:
    """Build and compile the Supervisor-Worker StateGraph.

    Graph structure:

        START
          │
          ▼
     ┌─────────────────┐
     │ supervisor_node  │  ← iterative LLM decision: call_agent | finish
     └────────┬────────┘
              │
     ┌────────▼────────┐
     │ supervisor_route │  ← reads supervisor_action from state
     └────────┬────────┘
       /   |   |   \    \
      ▼    ▼   ▼    ▼    ▼
    code  math crea gene  END
    agent agent tive ral   (finish)
      │     │    │    │
      └─────┴────┴────┘
              │
     specialist_should_continue
              │
        ┌─────┴─────┐
        ▼           ▼
   specialist_tool  supervisor_node
   (tool execution)  ↑
        │            │
        └────────────┘
        (route back to specialist
         based on route_category)
    """
    graph = StateGraph(AgentState)

    # ——— Nodes ———
    graph.add_node("supervisor_node", supervisor_node)  # type: ignore[arg-type]
    graph.add_node("code_agent", code_agent)  # type: ignore[arg-type]
    graph.add_node("math_agent", math_agent)  # type: ignore[arg-type]
    graph.add_node("creative_agent", creative_agent)  # type: ignore[arg-type]
    graph.add_node("general_agent", general_agent)  # type: ignore[arg-type]
    graph.add_node("specialist_tool_node", specialist_tool_node)  # type: ignore[arg-type]

    # ——— Edges ———

    # Start → Supervisor
    graph.add_edge(START, "supervisor_node")

    # Supervisor → Specialists or END
    graph.add_conditional_edges(
        "supervisor_node",
        supervisor_route,  # type: ignore[arg-type]
        {
            "code": "code_agent",
            "math": "math_agent",
            "creative": "creative_agent",
            "general": "general_agent",
            "__end__": END,
        },
    )

    # Each specialist → tool_node or back to supervisor
    specialists = ["code_agent", "math_agent", "creative_agent", "general_agent"]
    for specialist in specialists:
        graph.add_conditional_edges(
            specialist,
            specialist_should_continue,  # type: ignore[arg-type]
            {
                "tools": "specialist_tool_node",
                # Agent finished → report back to supervisor for next decision
                "__end__": "supervisor_node",
            },
        )

    # After tool execution → route back to the correct specialist
    # (based on route_category set by supervisor)
    graph.add_conditional_edges(
        "specialist_tool_node",
        supervisor_route,  # type: ignore[arg-type]
        {
            "code": "code_agent",
            "math": "math_agent",
            "creative": "creative_agent",
            "general": "general_agent",
            "__end__": END,
        },
    )

    return graph.compile()
