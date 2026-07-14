"""
Pydantic schemas for chat endpoints.
"""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Request body for the chat SSE endpoint.

    Supports two modes:
    1. conversation_id mode: send new_message, backend loads history from DB
    2. messages mode: (legacy) send full history directly
    """

    conversation_id: str | None = None
    new_message: "ChatNewMessage | None" = None
    messages: list[dict] | None = None  # legacy: full history
    model: str | None = None
    system: str | None = None


class ChatNewMessage(BaseModel):
    role: str = "user"
    content: str = ""
    parts: list[dict] = Field(default_factory=list)
