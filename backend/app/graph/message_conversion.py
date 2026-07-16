"""Conversion of legacy UI message parts into LangChain messages."""

import json
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)


def _text_from_ui_message(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if content and str(content).strip():
        return str(content)
    parts = message.get("parts")
    if not isinstance(parts, list):
        return ""
    return "\n".join(
        str(part.get("text", ""))
        for part in parts
        if isinstance(part, dict) and part.get("type") == "text"
    )


def ui_messages_to_langchain(raw_messages: list[dict[str, Any]]) -> list[BaseMessage]:
    """Convert the deprecated full-history request shape into valid messages."""
    messages: list[BaseMessage] = []
    for raw in raw_messages:
        role = raw.get("role", "user")
        if role == "system":
            messages.append(SystemMessage(content=_text_from_ui_message(raw)))
            continue
        if role == "user":
            messages.append(HumanMessage(content=_text_from_ui_message(raw)))
            continue
        if role == "tool":
            content = raw.get("content", "")
            messages.append(ToolMessage(
                content=json.dumps(content) if isinstance(content, dict) else str(content),
                tool_call_id=str(raw.get("toolCallId", "")),
            ))
            continue
        if role != "assistant":
            continue

        parts = raw.get("parts")
        if not isinstance(parts, list):
            messages.append(AIMessage(content=_text_from_ui_message(raw)))
            continue

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        tool_results: list[ToolMessage] = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type", ""))
            if part_type == "text":
                text_parts.append(str(part.get("text", "")))
            elif part_type.startswith("tool-"):
                call_id = str(part.get("toolCallId", ""))
                tool_calls.append({
                    "id": call_id,
                    "name": part_type[5:],
                    "args": part.get("input", {}),
                    "type": "tool_call",
                })
                if part.get("state") == "output-available" and part.get("output") is not None:
                    tool_results.append(ToolMessage(
                        content=json.dumps(part["output"], ensure_ascii=False),
                        tool_call_id=call_id,
                    ))
        messages.append(AIMessage(content="\n".join(text_parts), tool_calls=tool_calls))
        messages.extend(tool_results)
    return messages
