"""Request-scoped dependencies and budgets supplied to graph nodes."""

import asyncio
from dataclasses import dataclass, field
from typing import Literal, cast

from app.cache import MultiLayerCache
from app.tools.registry import MAX_CONCURRENT_TOOLS, MAX_TURN_TOOL_CALLS

SearchMode = Literal["auto", "web", "deep"]


def normalize_search_mode(value: object) -> SearchMode:
    return cast(SearchMode, value) if value in {"auto", "web", "deep"} else "auto"


@dataclass
class ToolBudget:
    """Concurrency-safe, request-local accounting shared by worker subgraphs."""

    total_calls: int = 0
    calls_by_tool: dict[str, int] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    semaphore: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(MAX_CONCURRENT_TOOLS),
        repr=False,
    )

    async def reserve(
        self,
        tool_name: str,
        *,
        per_tool_limit: int | None,
    ) -> str | None:
        """Reserve one executable call or return a stable rejection code."""
        async with self._lock:
            if self.total_calls >= MAX_TURN_TOOL_CALLS:
                return "turn_call_limit"
            current = self.calls_by_tool.get(tool_name, 0)
            if per_tool_limit is not None and current >= per_tool_limit:
                return f"{tool_name}_turn_limit"
            self.total_calls += 1
            self.calls_by_tool[tool_name] = current + 1
            return None


@dataclass
class AgentRuntimeContext:
    tool_cache: MultiLayerCache | None = None
    search_mode: SearchMode = "auto"
    tool_budget: ToolBudget = field(default_factory=ToolBudget)
    confirmed_tool_call_ids: frozenset[str] = field(default_factory=frozenset)
