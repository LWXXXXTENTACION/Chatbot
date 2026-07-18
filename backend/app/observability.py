"""Per-turn observability collection without a separate telemetry service.

The active collector is held in a context variable so every model call made by
the LangGraph turn -- including nested research and compaction calls -- is
accounted for by the shared LangChain callback.
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult


TRACE_SCHEMA_VERSION = 2
MAX_TIMELINE_EVENTS = 160


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_fingerprint() -> str:
    """Fingerprint the agent runtime so code changes form a new version."""
    app_dir = Path(__file__).resolve().parent
    roots = [
        app_dir / "agents",
        app_dir / "graph",
        app_dir / "llm",
        app_dir / "tools",
    ]
    # 缓存策略会直接改变工具调用耗时与命中行为，也必须形成新的可比较版本。
    files = [
        app_dir / "cache.py",
        app_dir / "config.py",
        app_dir / "observability.py",
    ]
    for root in roots:
        files.extend(root.rglob("*.py"))

    digest = hashlib.sha256()
    for path in sorted(files):
        if not path.is_file():
            continue
        digest.update(str(path.relative_to(app_dir)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


CODE_FINGERPRINT = _source_fingerprint()
RELEASE_LABEL = os.getenv("OBSERVABILITY_RELEASE", "").strip()


def _integer(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _usage_from_mapping(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    input_tokens = _integer(value.get("input_tokens", value.get("prompt_tokens")))
    output_tokens = _integer(
        value.get("output_tokens", value.get("completion_tokens"))
    )
    total_tokens = _integer(value.get("total_tokens")) or input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _result_usage(response: LLMResult) -> dict[str, int]:
    """Prefer normalized message usage, with provider output as fallback."""
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    found_message_usage = False
    for generations in response.generations:
        if not generations:
            continue
        message = getattr(generations[0], "message", None)
        usage = getattr(message, "usage_metadata", None)
        if usage:
            normalized = _usage_from_mapping(dict(usage))
            for key in totals:
                totals[key] += normalized[key]
            found_message_usage = True
    if found_message_usage:
        return totals

    llm_output = response.llm_output or {}
    raw_usage = (
        llm_output.get("token_usage")
        or llm_output.get("usage")
        or llm_output.get("usage_metadata")
    )
    return _usage_from_mapping(raw_usage)


class TraceCollector:
    """Collect bounded, content-free operational metadata for one graph turn."""

    def __init__(
        self,
        *,
        conversation_id: str,
        user_message_id: str,
        model: str,
        search_mode: str,
    ) -> None:
        self.run_id = uuid.uuid4().hex
        self.conversation_id = conversation_id
        self.user_message_id = user_message_id
        self.model = model
        self.search_mode = search_mode
        self.started_at = _now_iso()
        self._started = time.perf_counter()
        self._timeline: list[dict[str, Any]] = []
        self._llm_runs: dict[str, dict[str, Any]] = {}
        self._input_tokens = 0
        self._output_tokens = 0
        self._total_tokens = 0
        self._tool_calls = 0
        self._tool_errors = 0
        self._tool_rejections = 0
        self._tool_timeouts = 0
        self._tool_duration_ms = 0
        self._tool_output_chars = 0
        self._tool_truncations = 0
        self._cache_hits = 0
        self._sources = 0
        self._context: dict[str, Any] = {}

    def _elapsed_ms(self) -> int:
        return max(0, round((time.perf_counter() - self._started) * 1000))

    def _append(
        self,
        event_type: str,
        label: str,
        *,
        status: str = "completed",
        metadata: dict[str, Any] | None = None,
        event_id: str | None = None,
    ) -> None:
        if len(self._timeline) >= MAX_TIMELINE_EVENTS:
            return
        self._timeline.append({
            "id": event_id or uuid.uuid4().hex,
            "type": event_type,
            "label": label,
            "status": status,
            "at_ms": self._elapsed_ms(),
            "metadata": metadata or {},
        })

    def observe_event(self, event: dict[str, Any]) -> None:
        """Record typed SSE lifecycle events while deliberately dropping deltas."""
        event_type = str(event.get("type", ""))
        if event_type == "activity":
            self._append(
                f"activity.{event.get('kind', 'unknown')}",
                str(event.get("message") or "执行工作流阶段"),
            )
        elif event_type == "tool_call_start":
            self._tool_calls += 1
            self._append(
                "tool.start",
                f"调用 {event.get('toolName') or 'unknown'}",
                status="running",
                event_id=str(event.get("toolCallId") or uuid.uuid4().hex),
                metadata={"tool": str(event.get("toolName") or "unknown")},
            )
        elif event_type == "tool_result":
            cached = bool(event.get("cached"))
            cache_layer = str(event.get("cacheLayer") or "")
            error = event.get("error")
            rejection_reason = str(event.get("rejectionReason") or "")
            timeout_reason = str(event.get("timeoutReason") or "")
            duration_ms = _integer(event.get("durationMs"))
            output_chars = _integer(event.get("outputChars"))
            truncated = bool(event.get("outputTruncated"))
            self._cache_hits += int(cached)
            self._tool_errors += int(bool(error))
            self._tool_rejections += int(bool(rejection_reason))
            self._tool_timeouts += int(bool(timeout_reason))
            self._tool_duration_ms += duration_ms
            self._tool_output_chars += output_chars
            self._tool_truncations += int(truncated)
            if rejection_reason:
                label = f"工具调用被策略拒绝：{rejection_reason}"
            elif timeout_reason:
                label = "工具执行超时"
            elif cached:
                label = "工具返回缓存结果"
            else:
                label = "工具执行完成"
            self._append(
                "tool.end",
                label,
                status="error" if error else "completed",
                metadata={
                    "cached": cached,
                    "cache_layer": cache_layer or None,
                    "error": bool(error),
                    "duration_ms": duration_ms,
                    "output_chars": output_chars,
                    "model_output_chars": _integer(event.get("modelOutputChars")),
                    "output_truncated": truncated,
                    "rejection_reason": rejection_reason or None,
                    "timeout_reason": timeout_reason or None,
                },
            )
        elif event_type == "context_status":
            self._context = {
                "strategies": list(event.get("strategies") or []),
                "estimated_tokens_before": _integer(event.get("estimatedTokensBefore")),
                "estimated_tokens_after": _integer(event.get("estimatedTokensAfter")),
                "max_tokens": _integer(event.get("maxTokens")),
                "removed_messages": _integer(event.get("removedMessages")),
                "compacted_tool_results": _integer(event.get("compactedToolResults")),
                "overflowed": bool(event.get("overflowed")),
            }
            strategies = self._context["strategies"]
            self._append(
                "context",
                "上下文检查" if not strategies else f"上下文优化：{', '.join(strategies)}",
                status="error" if self._context["overflowed"] else "completed",
                metadata=self._context,
            )
        elif event_type == "sources":
            sources = event.get("sources")
            self._sources = len(sources) if isinstance(sources, list) else 0
            self._append(
                "sources",
                f"挂载 {self._sources} 个引用来源",
                metadata={"count": self._sources},
            )

    def model_started(
        self,
        run_id: str,
        *,
        model_name: str,
    ) -> None:
        if run_id in self._llm_runs:
            return
        self._llm_runs[run_id] = {
            "started": time.perf_counter(),
            "model": model_name or self.model,
            "status": "running",
        }
        self._append(
            "llm.start",
            f"LLM · {model_name or self.model}",
            status="running",
            event_id=run_id,
        )

    def model_finished(
        self,
        run_id: str,
        *,
        response: LLMResult | None = None,
        error: bool = False,
    ) -> None:
        run = self._llm_runs.setdefault(
            run_id,
            {"started": time.perf_counter(), "model": self.model, "status": "running"},
        )
        if run.get("status") != "running":
            return
        duration_ms = max(0, round((time.perf_counter() - run["started"]) * 1000))
        usage = _result_usage(response) if response is not None else _usage_from_mapping({})
        self._input_tokens += usage["input_tokens"]
        self._output_tokens += usage["output_tokens"]
        self._total_tokens += usage["total_tokens"]
        run.update({"status": "error" if error else "completed", **usage})
        self._append(
            "llm.end",
            "LLM 调用失败" if error else "LLM 响应完成",
            status="error" if error else "completed",
            metadata={"duration_ms": duration_ms, **usage},
        )

    def finish(self, status: str, *, error_code: str | None = None) -> dict[str, Any]:
        duration_ms = self._elapsed_ms()
        short_fingerprint = CODE_FINGERPRINT[:10]
        version_label = RELEASE_LABEL or f"agent-{short_fingerprint}"
        return {
            "schema_version": TRACE_SCHEMA_VERSION,
            "run_id": self.run_id,
            "version": {
                "id": f"{self.model}:{version_label}",
                "label": version_label,
                "code_fingerprint": CODE_FINGERPRINT,
            },
            "conversation_id": self.conversation_id,
            "user_message_id": self.user_message_id,
            "model": self.model,
            "search_mode": self.search_mode,
            "status": status,
            "error_code": error_code,
            "started_at": self.started_at,
            "completed_at": _now_iso(),
            "duration_ms": duration_ms,
            "metrics": {
                "input_tokens": self._input_tokens,
                "output_tokens": self._output_tokens,
                "total_tokens": self._total_tokens,
                "llm_calls": len(self._llm_runs),
                "tool_calls": self._tool_calls,
                "tool_errors": self._tool_errors,
                "tool_rejections": self._tool_rejections,
                "tool_timeouts": self._tool_timeouts,
                "tool_duration_ms": self._tool_duration_ms,
                "tool_output_chars": self._tool_output_chars,
                "tool_truncations": self._tool_truncations,
                "cache_hits": self._cache_hits,
                "sources": self._sources,
            },
            "context": self._context,
            "timeline": self._timeline,
            "evaluation": None,
        }


_CURRENT_TRACE: ContextVar[TraceCollector | None] = ContextVar(
    "chatbot_observability_trace",
    default=None,
)


@contextmanager
def bind_trace(collector: TraceCollector) -> Iterator[None]:
    token = _CURRENT_TRACE.set(collector)
    try:
        yield
    finally:
        _CURRENT_TRACE.reset(token)


def current_trace() -> TraceCollector | None:
    return _CURRENT_TRACE.get()


class ObservabilityCallbackHandler(AsyncCallbackHandler):
    """LangChain callback that attributes nested model calls to the active run."""

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        del messages
        collector = current_trace()
        if collector is None:
            return
        invocation = kwargs.get("invocation_params") or {}
        model_name = str(
            invocation.get("model")
            or invocation.get("model_name")
            or serialized.get("name")
            or collector.model
        )
        collector.model_started(str(run_id), model_name=model_name)

    async def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        del prompts
        collector = current_trace()
        if collector is None:
            return
        invocation = kwargs.get("invocation_params") or {}
        model_name = str(
            invocation.get("model")
            or invocation.get("model_name")
            or serialized.get("name")
            or collector.model
        )
        collector.model_started(str(run_id), model_name=model_name)

    async def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        del kwargs
        collector = current_trace()
        if collector is not None:
            collector.model_finished(str(run_id), response=response)

    async def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        del error, kwargs
        collector = current_trace()
        if collector is not None:
            collector.model_finished(str(run_id), error=True)


OBSERVABILITY_CALLBACK = ObservabilityCallbackHandler()
