"""Typed shared state for the Supervisor multi-agent workflow."""

from typing import Annotated, Any, Literal, NotRequired, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from app.config import DeepSeekModelId


class SourceCitation(TypedDict):
    """Normalized source shape persisted and rendered with an answer."""

    title: str
    url: str
    content: str
    score: NotRequired[Any]


AgentName = Literal["supervisor", "general_agent", "research_agent"]
WorkerRoute = Literal["general_agent", "research_agent"]
ContextStrategy = Literal[
    "microcompact",
    "context_collapse",
    "session_memory",
    "full_compact",
    "ptl_truncation",
]


class SupervisorDecision(TypedDict):
    """Auditable assignment produced by the Supervisor."""

    route: WorkerRoute
    task: str
    reason: str


class ContextReport(TypedDict):
    """Observable result of one context-pressure evaluation."""

    strategies: list[ContextStrategy]
    estimated_tokens_before: int
    estimated_tokens_after: int
    max_tokens: int
    pressure_before: float
    pressure_after: float
    compacted_tool_results: int
    removed_messages: int
    overflowed: bool


class AgentInput(TypedDict):
    """Values supplied by the API for one workflow invocation.

    ``messages`` is the only reducer-backed field. With a checkpointer, the API
    can therefore send only the new user message for a synchronized thread;
    LangGraph appends it to the durable message history.
    """

    messages: Annotated[list[BaseMessage], add_messages]
    model_id: DeepSeekModelId
    system_prompt: str
    user_id: str
    conversation_id: str


class AgentState(AgentInput):
    """Complete shared state checkpointed between workflow steps.

    Coordination fields are turn-local. ``prepare_turn`` resets them after a
    thread is restored, so assignments and worker results cannot leak across
    conversation turns.
    """

    supervisor_decision: SupervisorDecision | None
    active_agent: AgentName | None
    completed_agents: list[AgentName]
    worker_result: str
    source_citations: list[SourceCitation]
    context_summary: str
    session_memory: str
    session_memory_cursor: str
    context_report: ContextReport | None
    error: str | None


class AgentOutput(TypedDict):
    """Minimal result consumed by the API persistence layer."""

    messages: Annotated[list[BaseMessage], add_messages]
    error: str | None
