"""
Edge routing logic for the LangGraph agent.
"""

from typing import Literal

from langchain_core.messages import AIMessage

from app.graph.state import AgentState


def should_continue(state: AgentState) -> Literal["tools", "__end__"]:
    """Determine whether to execute tools or end the turn.

    Called after chat_node: if the last message has tool_calls,
    route to tool_node; otherwise end.
    """
    messages = state.get("messages", [])
    if not messages:
        return "__end__"

    last_message = messages[-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "tools"
    return "__end__"
