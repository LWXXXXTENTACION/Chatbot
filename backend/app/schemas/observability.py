"""Request models for human or judge evaluation of captured runs."""

from pydantic import BaseModel, Field


class RunEvaluationUpdate(BaseModel):
    passed: bool | None
    note: str = Field(default="", max_length=1000)
    case_id: str = Field(default="", max_length=128)
