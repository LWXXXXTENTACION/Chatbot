"""Request-scoped dependencies supplied to graph nodes."""

from dataclasses import dataclass
from typing import Literal, cast

from app.cache import ToolCache

SearchMode = Literal["auto", "web", "deep"]


def normalize_search_mode(value: object) -> SearchMode:
    return cast(SearchMode, value) if value in {"auto", "web", "deep"} else "auto"


@dataclass
class AgentRuntimeContext:
    tool_cache: ToolCache | None = None
    search_mode: SearchMode = "auto"
