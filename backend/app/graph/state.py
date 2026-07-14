"""State shared by the main agent and its tool dispatcher."""

from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    model_id: str
    system_prompt: str
    user_id: str
    conversation_id: str
    source_citations: list[dict[str, Any]]
    retrieved_docs: str
    search_iteration: int
    search_history: list[str]
    error: str | None
