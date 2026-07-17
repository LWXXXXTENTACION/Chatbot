"""Central tool policies and the worker-scoped registry.

The registry is the only place where executable tools are exposed to the graph.
It keeps safety and resource limits next to the tool identity instead of
spreading special cases across worker nodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from langchain_core.tools import BaseTool

from app.tools.artifact import MAX_ARTIFACT_CONTENT_CHARS, create_artifact
from app.tools.calculate import calculate
from app.tools.deep_search import deep_search
from app.tools.weather import get_weather
from app.tools.web_search import web_search

WorkerName = Literal["general_agent", "research_agent"]

MAX_BATCH_TOOL_CALLS = 3
MAX_TURN_TOOL_CALLS = 6
MAX_CONCURRENT_TOOLS = 3


@dataclass(frozen=True)
class ToolPolicy:
    """Execution policy applied before a tool is invoked."""

    tool: BaseTool
    workers: frozenset[WorkerName]
    timeout_seconds: float
    concurrency_safe: bool = True
    produces_state_patch: bool = False
    max_calls_per_turn: int | None = None
    max_model_output_chars: int = 12_000
    max_display_output_chars: int = 48_000
    requires_confirmation: bool = False

    @property
    def name(self) -> str:
        return self.tool.name


class ToolRegistry:
    """Immutable lookup facade used by every worker tool node."""

    def __init__(self, policies: list[ToolPolicy]) -> None:
        self._policies = {policy.name: policy for policy in policies}
        if len(self._policies) != len(policies):
            raise ValueError("工具注册表包含重复名称")

    def get(self, name: str, worker: WorkerName) -> ToolPolicy | None:
        policy = self._policies.get(name)
        if policy is None or worker not in policy.workers:
            return None
        return policy

    def tools_for(self, worker: WorkerName) -> list[BaseTool]:
        return [
            policy.tool
            for policy in self._policies.values()
            if worker in policy.workers
        ]


TOOL_REGISTRY = ToolRegistry([
    ToolPolicy(
        tool=get_weather,
        workers=frozenset({"general_agent"}),
        timeout_seconds=5,
    ),
    ToolPolicy(
        tool=calculate,
        workers=frozenset({"general_agent"}),
        timeout_seconds=2,
        max_model_output_chars=4_000,
        max_display_output_chars=8_000,
    ),
    ToolPolicy(
        tool=create_artifact,
        workers=frozenset({"general_agent"}),
        timeout_seconds=3,
        max_calls_per_turn=1,
        max_model_output_chars=4_000,
        max_display_output_chars=8_000,
    ),
    ToolPolicy(
        tool=web_search,
        workers=frozenset({"research_agent"}),
        timeout_seconds=15,
        concurrency_safe=False,
        produces_state_patch=True,
    ),
    ToolPolicy(
        tool=deep_search,
        workers=frozenset({"research_agent"}),
        timeout_seconds=60,
        concurrency_safe=False,
        produces_state_patch=True,
        max_calls_per_turn=1,
        max_model_output_chars=16_000,
        max_display_output_chars=64_000,
    ),
])

__all__ = [
    "MAX_ARTIFACT_CONTENT_CHARS",
    "MAX_BATCH_TOOL_CALLS",
    "MAX_CONCURRENT_TOOLS",
    "MAX_TURN_TOOL_CALLS",
    "TOOL_REGISTRY",
    "ToolPolicy",
    "ToolRegistry",
    "WorkerName",
]
