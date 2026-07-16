"""Transient dependencies supplied to graph nodes without checkpointing them."""

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal, cast

from app.cache import ToolCache
from app.graph.events import StreamEvent

StreamCallback = Callable[[StreamEvent], Awaitable[None]]
SearchMode = Literal["auto", "web", "deep"]


def normalize_search_mode(value: object) -> SearchMode:
    return cast(SearchMode, value) if value in {"auto", "web", "deep"} else "auto"


@dataclass
class AgentRuntimeContext:
    stream_callback: StreamCallback | None = None
    tool_cache: ToolCache | None = None
    search_mode: SearchMode = "auto"
