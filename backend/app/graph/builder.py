"""Build the main-agent graph with one optional deep-search delegate."""

from langgraph.graph import END, START, StateGraph

from app.graph.nodes import chat_node, custom_tool_node
from app.graph.routing import should_continue
from app.graph.state import AgentState


def build_graph():
    """Compile START → main_agent → tools → main_agent, ending on an answer."""
    graph = StateGraph(AgentState)
    graph.add_node("main_agent", chat_node)
    graph.add_node("tools", custom_tool_node)
    graph.add_edge(START, "main_agent")
    graph.add_conditional_edges(
        "main_agent",
        should_continue,
        {"tools": "tools", "__end__": END},
    )
    graph.add_edge("tools", "main_agent")
    return graph.compile()
