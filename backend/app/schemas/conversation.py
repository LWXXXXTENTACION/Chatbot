"""
Pydantic schemas for conversation endpoints.
"""

from pydantic import BaseModel, Field


# ——— Message Part ———

class MessagePartResponse(BaseModel):
    id: str
    type: str
    text: str | None = None
    tool_call_id: str | None = None
    tool_state: str | None = None
    tool_input: object = None
    tool_output: object = None
    tool_error: str | None = None
    position: int = 0

    model_config = {"from_attributes": True}


# ——— Message ———

class MessageResponse(BaseModel):
    id: str
    role: str
    created_at: str
    parts: list[MessagePartResponse] = []

    model_config = {"from_attributes": True}


# ——— Conversation ———

class ConversationCreate(BaseModel):
    title: str = "新对话"
    model: str = "deepseek-v4-flash"


class ConversationUpdate(BaseModel):
    title: str | None = None
    model: str | None = None


class ConversationResponse(BaseModel):
    id: str
    title: str
    model: str
    message_count: int = 0
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class ConversationDetailResponse(BaseModel):
    id: str
    title: str
    model: str
    message_count: int = 0
    created_at: str
    updated_at: str
    messages: list[MessageResponse] = []

    model_config = {"from_attributes": True}
