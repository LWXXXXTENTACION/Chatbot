"""
AgentState for the LangGraph graph.
Uses a TypedDict with annotated messages list (add_messages reducer).
"""

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    """State passed between nodes in the LangGraph agent.

    - messages: The full conversation history, with add_messages reducer
      that merges new AIMessage/ToolMessage entries correctly.
    - model_id: Which DeepSeek model to use for this turn.
    - system_prompt: System prompt for the LLM.
    - user_id: Authenticated user ID (for multi-tenant scoping).
    - conversation_id: Current conversation ID (for persistence).
    - route_category: RouterAgent classification result ("code"|"math"|"creative"|"general").
      Legacy field — kept for backward compat, now set by the supervisor node.
    - agent_outputs: Collected outputs from specialist agents.
    - source_citations: Raw citation metadata [{title, url, content}, ...].
    - retrieved_docs: Formatted context string for LLM injection.
    - search_iteration: Current search round counter (limits max searches to avoid infinite loops).
    - search_history: Historical search queries (used to avoid duplicate searches).
    - error: Error state, if any.

    ——— Supervisor fields ———
    - supervisor_action: Current decision ("call_agent" | "finish").
    - supervisor_target: Which specialist to dispatch ("code"|"math"|"creative"|"general").
    - supervisor_task: Task description sent to the specialist.
    - supervisor_iteration: How many times the supervisor has been invoked (safety counter).
    """

    messages: Annotated[list[BaseMessage], add_messages]
    model_id: str
    system_prompt: str
    user_id: str
    conversation_id: str
    route_category: str
    agent_outputs: dict[str, str]
    source_citations: list[dict[str, str]]
    retrieved_docs: str
    search_iteration: int
    search_history: list[str]
    error: str | None

    # Supervisor orchestration
    supervisor_action: str
    supervisor_target: str
    supervisor_task: str
    supervisor_iteration: int
