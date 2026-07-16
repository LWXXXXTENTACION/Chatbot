"""Typed server-sent events emitted by workflow nodes.

The field names intentionally match ``src/lib/types.ts``. Keeping the event
union here makes the backend's streaming contract visible instead of passing
unstructured ``dict[str, Any]`` values through every layer.
"""

from typing import Any, Literal, NotRequired, TypedDict

from app.graph.state import ContextStrategy, SourceCitation


class TextStartEvent(TypedDict):
    type: Literal["text_start"]
    messageId: str


class TextDeltaEvent(TypedDict):
    type: Literal["text_delta"]
    messageId: str
    delta: str


class TextEndEvent(TypedDict):
    type: Literal["text_end"]
    messageId: str


class ReasoningStartEvent(TypedDict):
    type: Literal["reasoning_start"]
    messageId: str


class ReasoningDeltaEvent(TypedDict):
    type: Literal["reasoning_delta"]
    messageId: str
    delta: str


class ReasoningEndEvent(TypedDict):
    type: Literal["reasoning_end"]
    messageId: str


class ToolCallStartEvent(TypedDict):
    type: Literal["tool_call_start"]
    messageId: str
    toolCallId: str
    toolName: str


class ToolCallDeltaEvent(TypedDict):
    type: Literal["tool_call_delta"]
    toolCallId: str
    delta: str


class ToolCallEndEvent(TypedDict):
    type: Literal["tool_call_end"]
    toolCallId: str


class ToolResultEvent(TypedDict):
    type: Literal["tool_result"]
    toolCallId: str
    result: Any
    cached: NotRequired[bool]
    error: str | None


class SourcesEvent(TypedDict):
    type: Literal["sources"]
    messageId: str
    sources: list[SourceCitation]


class ActivityEvent(TypedDict):
    type: Literal["activity"]
    kind: Literal[
        "searching",
        "retrieved",
        "analyzing",
        "answering",
        "rewriting",
        "compacting",
    ]
    message: str


class ContextStatusEvent(TypedDict):
    type: Literal["context_status"]
    strategies: list[ContextStrategy]
    estimatedTokensBefore: int
    estimatedTokensAfter: int
    maxTokens: int
    pressureBefore: float
    pressureAfter: float
    compactedToolResults: int
    removedMessages: int
    overflowed: bool


class DoneEvent(TypedDict):
    type: Literal["done"]
    messageId: str


class ErrorEvent(TypedDict):
    type: Literal["error"]
    message: str
    code: str


StreamEvent = (
    TextStartEvent
    | TextDeltaEvent
    | TextEndEvent
    | ReasoningStartEvent
    | ReasoningDeltaEvent
    | ReasoningEndEvent
    | ToolCallStartEvent
    | ToolCallDeltaEvent
    | ToolCallEndEvent
    | ToolResultEvent
    | SourcesEvent
    | ActivityEvent
    | ContextStatusEvent
    | DoneEvent
    | ErrorEvent
)
