"""
SQLAlchemy ORM models for the chatbot application.

Tables:
  - users: user accounts
  - conversations: chat conversations (scoped to user)
  - messages: individual messages in a conversation
  - message_parts: denormalized parts of a message (text, reasoning, tool calls)
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(String(32), primary_key=True, default=_uuid)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(128), nullable=False)
    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now, nullable=False)
    conversations = relationship(
        "Conversation", back_populates="user", cascade="all, delete-orphan"
    )


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(String(32), primary_key=True, default=_uuid)
    user_id = Column(
        String(32), ForeignKey("users.id"), nullable=False, index=True
    )
    title = Column(String(128), default="新对话", nullable=False)
    model = Column(String(32), default="deepseek-v4-flash", nullable=False)
    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now, nullable=False)
    message_sequence = Column(Integer, default=0, nullable=False)

    user = relationship("User", back_populates="conversations")
    messages = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.sequence",
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "sequence",
            name="uq_messages_conversation_sequence",
        ),
    )

    id = Column(String(32), primary_key=True, default=_uuid)
    conversation_id = Column(
        String(32),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(String(16), nullable=False)  # "user" | "assistant" | "system" | "tool"
    sequence = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=_now, nullable=False)

    conversation = relationship("Conversation", back_populates="messages")
    parts = relationship(
        "MessagePart",
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="MessagePart.position",
    )


class MessagePart(Base):
    __tablename__ = "message_parts"

    id = Column(String(32), primary_key=True, default=_uuid)
    message_id = Column(
        String(32),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type = Column(String(32), nullable=False)  # "text" | "reasoning" | "tool-{name}"
    text = Column(Text, nullable=True)
    tool_call_id = Column(String(64), nullable=True)
    tool_state = Column(String(24), nullable=True)
    tool_input = Column(JSON, nullable=True)
    tool_output = Column(JSON, nullable=True)
    tool_error = Column(Text, nullable=True)
    position = Column(Integer, nullable=False, default=0)

    message = relationship("Message", back_populates="parts")
