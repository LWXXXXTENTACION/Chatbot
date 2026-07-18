"""
Pydantic schemas for chat endpoints.
"""

from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Request body for the chat SSE endpoint.

    Supports two modes:
    1. conversation_id mode: send new_message, backend loads history from DB
    2. messages mode: (legacy) send full history directly
    """

    conversation_id: str | None = None
    stream_id: str | None = Field(default=None, min_length=8, max_length=128)
    new_message: "ChatNewMessage | None" = None
    messages: list[dict] | None = None  # legacy: full history
    model: str | None = None
    system: str | None = None
    search_mode: Literal["auto", "web", "deep"] = "auto"


class ChatNewMessage(BaseModel):
    role: str = "user"
    content: str = ""
    parts: list[dict] = Field(default_factory=list)
