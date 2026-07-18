"""LangGraph 自定义事件协议与统一发送入口。

字段名与 ``src/lib/types.ts`` 一一对应。把事件联合类型集中在这里，可以直接看清
后端到前端的流式契约，而不是让无结构的 ``dict[str, Any]`` 穿过每一层。
"""

import json
import logging
from typing import Any, Literal, NotRequired, TypedDict

from langchain_core.messages import AIMessage
from langgraph.types import StreamWriter

from app.graph.state import ContextStrategy, SourceCitation

ActivityKind = Literal[
    "searching",
    "retrieved",
    "analyzing",
    "answering",
    "rewriting",
    "compacting",
]


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
    cacheLayer: NotRequired[Literal["l1", "l2", "l3"] | None]
    error: str | None
    status: NotRequired[Literal["success", "error", "rejected", "timeout"]]
    durationMs: NotRequired[int]
    outputChars: NotRequired[int]
    modelOutputChars: NotRequired[int]
    outputTruncated: NotRequired[bool]
    rejectionReason: NotRequired[str | None]
    timeoutReason: NotRequired[str | None]


class SourcesEvent(TypedDict):
    type: Literal["sources"]
    messageId: str
    sources: list[SourceCitation]


class ActivityEvent(TypedDict):
    type: Literal["activity"]
    kind: ActivityKind
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


logger = logging.getLogger("chatbot.graph.events")


async def emit(writer: StreamWriter, event: StreamEvent) -> None:
    """从唯一入口写入 LangGraph ``custom`` 事件流。"""
    try:
        writer(event)
    except Exception:
        # UI 可观测事件失败不应破坏已经完成的业务节点。
        logger.exception("LangGraph custom stream writer failed")


async def emit_activity(
    writer: StreamWriter,
    *,
    kind: ActivityKind,
    message: str,
) -> None:
    """发送统一格式的工作流阶段提示。"""
    await emit(writer, {"type": "activity", "kind": kind, "message": message})


async def emit_tool_call(writer: StreamWriter, message: AIMessage) -> None:
    """把确定性 AI tool_call 转成前端可消费的三段式事件。"""
    message_id = str(message.id or "")
    for call in message.tool_calls:
        call_id = str(call.get("id", ""))
        args_json = json.dumps(
            call.get("args", {}),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        await emit(writer, {
            "type": "tool_call_start",
            "messageId": message_id,
            "toolCallId": call_id,
            "toolName": str(call.get("name", "unknown")),
        })
        await emit(writer, {
            "type": "tool_call_delta",
            "toolCallId": call_id,
            "delta": args_json,
        })
        await emit(writer, {
            "type": "tool_call_end",
            "toolCallId": call_id,
        })
