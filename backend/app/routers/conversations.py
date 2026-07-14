"""
Conversation CRUD router.
All endpoints require authentication. Data is scoped to the current user.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.engine import get_db
from app.database.models import Conversation, Message, MessagePart, User
from app.middleware.auth import get_current_user
from app.schemas.conversation import (
    ConversationCreate,
    ConversationDetailResponse,
    ConversationResponse,
    ConversationUpdate,
    MessagePartResponse,
    MessageResponse,
)

logger = logging.getLogger("chatbot.conversations")
router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _conv_to_response(conv: Conversation) -> ConversationResponse:
    """Convert an ORM Conversation to a ConversationResponse."""
    # messages may not be loaded if not eagerly loaded
    try:
        msg_count = len(conv.messages) if conv.messages is not None else 0
    except Exception:
        msg_count = 0
    return ConversationResponse(
        id=conv.id,
        title=conv.title,
        model=conv.model,
        message_count=msg_count,
        created_at=conv.created_at.isoformat() if conv.created_at else "",
        updated_at=conv.updated_at.isoformat() if conv.updated_at else "",
    )


def _conv_to_detail(conv: Conversation) -> ConversationDetailResponse:
    """Convert an ORM Conversation to a ConversationDetailResponse with messages."""
    msgs = conv.messages or []
    return ConversationDetailResponse(
        id=conv.id,
        title=conv.title,
        model=conv.model,
        message_count=len(msgs),
        created_at=conv.created_at.isoformat() if conv.created_at else "",
        updated_at=conv.updated_at.isoformat() if conv.updated_at else "",
        messages=[
            MessageResponse(
                id=m.id,
                role=m.role,
                created_at=m.created_at.isoformat() if m.created_at else "",
                parts=[
                    MessagePartResponse(
                        id=p.id,
                        type=p.type,
                        text=p.text,
                        tool_call_id=p.tool_call_id,
                        tool_state=p.tool_state,
                        tool_input=p.tool_input,
                        tool_output=p.tool_output,
                        tool_error=p.tool_error,
                        position=p.position,
                    )
                    for p in (m.parts or [])
                ],
            )
            for m in msgs
        ],
    )


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all conversations for the current user, ordered by most recent first."""
    result = await db.execute(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(Conversation.user_id == current_user.id)
        .order_by(desc(Conversation.updated_at))
    )
    conversations = result.scalars().all()
    return [_conv_to_response(conv) for conv in conversations]


@router.post("", response_model=ConversationResponse)
async def create_conversation(
    body: ConversationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new conversation for the current user."""
    conv = Conversation(
        user_id=current_user.id,
        title=body.title,
        model=body.model,
    )
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    logger.info(f"Conversation created: {conv.id} by {current_user.username}")
    return _conv_to_response(conv)


@router.get("/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single conversation with all messages and parts."""
    result = await db.execute(
        select(Conversation)
        .options(
            selectinload(Conversation.messages).selectinload(Message.parts)
        )
        .where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="对话不存在")
    return _conv_to_detail(conv)


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: str,
    body: ConversationUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update conversation title and/or model."""
    result = await db.execute(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="对话不存在")

    if body.title is not None:
        conv.title = body.title
    if body.model is not None:
        conv.model = body.model

    await db.commit()
    await db.refresh(conv)
    return _conv_to_response(conv)


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a conversation and all its messages (cascade)."""
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
        )
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="对话不存在")

    await db.delete(conv)
    await db.commit()
    logger.info(f"Conversation deleted: {conversation_id} by {current_user.username}")
    return {"ok": True}
