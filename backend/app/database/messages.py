"""Ordered, idempotent persistence for chat messages and tool parts."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import Conversation, Message, MessagePart


async def reserve_sequences(
    db: AsyncSession,
    conversation_id: str,
    count: int,
) -> int:
    """Atomically reserve ``count`` sequence numbers and return the first."""
    if count <= 0:
        return 0
    result = await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(message_sequence=Conversation.message_sequence + count)
        .returning(Conversation.message_sequence)
    )
    next_sequence = result.scalar_one_or_none()
    if next_sequence is None:
        raise ValueError("Conversation does not exist")
    return int(next_sequence) - count


async def persist_user_message(
    db: AsyncSession,
    conversation_id: str,
    message: HumanMessage,
) -> None:
    """Persist one user message before starting the graph run."""
    message_id = str(message.id or uuid.uuid4().hex)
    message.id = message_id
    sequence = await reserve_sequences(db, conversation_id, 1)
    row = Message(
        id=message_id,
        conversation_id=conversation_id,
        role="user",
        sequence=sequence,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    content = message.content
    if isinstance(content, str) and content.strip():
        db.add(MessagePart(
            id=uuid.uuid4().hex,
            message_id=message_id,
            type="text",
            text=content,
            position=0,
        ))
    await db.commit()


async def persist_graph_messages(
    db: AsyncSession,
    conversation_id: str,
    messages: list[BaseMessage],
) -> None:
    """Persist assistant messages and attach tool results in one transaction."""
    assistant_messages = [message for message in messages if isinstance(message, AIMessage)]
    if not assistant_messages:
        return

    sequence = await reserve_sequences(db, conversation_id, len(assistant_messages))
    tool_parts: dict[str, MessagePart] = {}
    now = datetime.now(timezone.utc)

    for lc_message in messages:
        if isinstance(lc_message, AIMessage):
            message_id = str(lc_message.id or uuid.uuid4().hex)
            lc_message.id = message_id
            row = Message(
                id=message_id,
                conversation_id=conversation_id,
                role="assistant",
                sequence=sequence,
                created_at=now,
            )
            sequence += 1
            db.add(row)
            position = 0

            text = lc_message.content
            if isinstance(text, str) and text.strip():
                db.add(MessagePart(
                    id=uuid.uuid4().hex,
                    message_id=message_id,
                    type="text",
                    text=text,
                    position=position,
                ))
                position += 1

            reasoning = lc_message.additional_kwargs.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning.strip():
                db.add(MessagePart(
                    id=uuid.uuid4().hex,
                    message_id=message_id,
                    type="reasoning",
                    text=reasoning,
                    position=position,
                ))
                position += 1

            sources = lc_message.additional_kwargs.get("sources")
            if isinstance(sources, list) and sources:
                db.add(MessagePart(
                    id=uuid.uuid4().hex,
                    message_id=message_id,
                    type="sources",
                    tool_output={"results": sources},
                    position=position,
                ))
                position += 1

            for tool_call in lc_message.tool_calls:
                call_id = str(tool_call.get("id", ""))
                part = MessagePart(
                    id=uuid.uuid4().hex,
                    message_id=message_id,
                    type=f"tool-{tool_call.get('name', '')}",
                    tool_call_id=call_id,
                    tool_state="input-available",
                    tool_input=tool_call.get("args", {}),
                    position=position,
                )
                db.add(part)
                tool_parts[call_id] = part
                position += 1

        elif isinstance(lc_message, ToolMessage):
            part = tool_parts.get(str(lc_message.tool_call_id))
            if part is None:
                continue
            try:
                output = (
                    json.loads(lc_message.content)
                    if isinstance(lc_message.content, str)
                    else lc_message.content
                )
            except (json.JSONDecodeError, TypeError):
                output = lc_message.content
            part.tool_output = output
            if getattr(lc_message, "status", "success") == "error":
                part.tool_state = "output-error"
                part.tool_error = (
                    output.get("error", "Tool execution failed")
                    if isinstance(output, dict)
                    else str(output)
                )
            else:
                part.tool_state = "output-available"

    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(updated_at=now)
    )
    await db.commit()


def db_message_to_langchain(message: Message) -> list[BaseMessage]:
    """Rebuild a valid AI/tool sequence from one ordered business message."""
    parts = sorted(message.parts or [], key=lambda part: part.position)
    if message.role == "user":
        text = "".join(part.text or "" for part in parts if part.type == "text")
        return [HumanMessage(content=text, id=message.id)]
    if message.role != "assistant":
        return []

    text = "".join(part.text or "" for part in parts if part.type == "text")
    additional_kwargs: dict = {}
    reasoning = "".join(part.text or "" for part in parts if part.type == "reasoning")
    if reasoning:
        additional_kwargs["reasoning_content"] = reasoning
    source_part = next((part for part in parts if part.type == "sources"), None)
    if source_part and isinstance(source_part.tool_output, dict):
        additional_kwargs["sources"] = source_part.tool_output.get("results", [])

    tool_parts = [part for part in parts if part.type.startswith("tool-")]
    tool_calls = [
        {
            "id": part.tool_call_id or "",
            "name": part.type[5:],
            "args": part.tool_input or {},
            "type": "tool_call",
        }
        for part in tool_parts
    ]
    ai_message = AIMessage(
        content=text,
        additional_kwargs=additional_kwargs,
        tool_calls=tool_calls,
        id=message.id,
    )
    result: list[BaseMessage] = [ai_message]
    for part in tool_parts:
        if part.tool_state not in {"output-available", "output-error"}:
            continue
        output = part.tool_output
        if output is None and part.tool_error:
            output = {"error": part.tool_error}
        result.append(ToolMessage(
            content=json.dumps(output if output is not None else {}, ensure_ascii=False),
            tool_call_id=part.tool_call_id or "",
            name=part.type[5:],
            status="error" if part.tool_state == "output-error" else "success",
            id=uuid.uuid5(uuid.NAMESPACE_URL, f"{message.id}:{part.id}").hex,
        ))
    return result


async def conversation_messages(
    db: AsyncSession,
    conversation_id: str,
) -> list[BaseMessage]:
    result = await db.execute(
        select(Message)
        .options(selectinload(Message.parts))
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.sequence)
    )
    messages: list[BaseMessage] = []
    for row in result.scalars().all():
        messages.extend(db_message_to_langchain(row))
    return messages
