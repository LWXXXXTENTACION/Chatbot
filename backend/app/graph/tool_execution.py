"""统一工具策略节点的底层实现。

Agent 只负责产生 tool_calls；真正执行前必须经过白名单、Schema、额度、确认、
缓存、并发与超时检查。模型看到的裁剪结果、UI 看到的展示结果和允许写回 Graph
的 State Patch 被明确分离，避免工具输出直接任意修改共享状态。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import StreamWriter
from pydantic import ValidationError

from app.config import DeepSeekModelId
from app.graph.context import AgentRuntimeContext
from app.graph.deep_search import dedupe_sources, run_deep_search_workflow
from app.graph.events import emit
from app.graph.state import AgentState, SourceCitation
from app.tools.registry import (
    MAX_BATCH_TOOL_CALLS,
    TOOL_REGISTRY,
    ToolPolicy,
    WorkerName,
)

logger = logging.getLogger("chatbot.graph.tools")

OutcomeStatus = Literal["success", "error", "rejected", "timeout"]


@dataclass(frozen=True)
class ToolOutcome:
    """一次工具结果的三层视图：模型上下文、UI 展示和受控状态更新。"""

    model_content: str
    display_output: dict[str, Any]
    state_patch: dict[str, Any] | None
    cached: bool
    status: OutcomeStatus
    duration_ms: int
    output_chars: int
    model_output_chars: int
    output_truncated: bool = False
    rejection_reason: str | None = None
    timeout_reason: str | None = None


@dataclass(frozen=True)
class PreparedCall:
    """通过工具归属与 Pydantic Schema 检查后的不可变调用。"""

    index: int
    call_id: str
    name: str
    args: dict[str, Any]
    policy: ToolPolicy


def _json_content(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return json.dumps(
            {"result": str(value)},
            ensure_ascii=False,
            separators=(",", ":"),
        )


def _as_display_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {"result": value}


def _bounded_model_content(value: Any, limit: int) -> tuple[str, int, bool]:
    encoded = _json_content(value)
    original_chars = len(encoded)
    if original_chars <= limit:
        return encoded, original_chars, False

    low = 0
    high = min(original_chars, limit)
    best = ""
    while low <= high:
        size = (low + high) // 2
        candidate = _json_content({
            "truncated": True,
            "original_chars": original_chars,
            "preview": encoded[:size],
        })
        if len(candidate) <= limit:
            best = candidate
            low = size + 1
        else:
            high = size - 1
    return best or _json_content({"truncated": True}), original_chars, True


def _bounded_display_output(
    value: Any,
    limit: int,
) -> tuple[dict[str, Any], bool]:
    output = _as_display_dict(value)
    encoded = _json_content(output)
    if len(encoded) <= limit:
        return output, False
    low = 0
    high = min(len(encoded), limit)
    best: dict[str, Any] = {"truncated": True}
    while low <= high:
        size = (low + high) // 2
        candidate = {
            "truncated": True,
            "originalChars": len(encoded),
            "preview": encoded[:size],
        }
        if len(_json_content(candidate)) <= limit:
            best = candidate
            low = size + 1
        else:
            high = size - 1
    return best, True


def _rejected_outcome(code: str, message: str) -> ToolOutcome:
    output = {"error": message, "code": code}
    content = _json_content(output)
    return ToolOutcome(
        model_content=content,
        display_output=output,
        state_patch=None,
        cached=False,
        status="rejected",
        duration_ms=0,
        output_chars=len(content),
        model_output_chars=len(content),
        rejection_reason=code,
    )


def _timeout_outcome(policy: ToolPolicy, duration_ms: int) -> ToolOutcome:
    reason = f"超过 {policy.timeout_seconds:g} 秒工具超时上限"
    output = {"error": f"工具 {policy.name} 执行超时", "code": "tool_timeout"}
    content = _json_content(output)
    return ToolOutcome(
        model_content=content,
        display_output=output,
        state_patch=None,
        cached=False,
        status="timeout",
        duration_ms=duration_ms,
        output_chars=len(content),
        model_output_chars=len(content),
        timeout_reason=reason,
    )


def _message(call: PreparedCall, outcome: ToolOutcome) -> ToolMessage:
    return ToolMessage(
        content=outcome.model_content,
        tool_call_id=call.call_id,
        name=call.name,
        status="success" if outcome.status == "success" else "error",
        additional_kwargs={
            "context_created_at": datetime.now(timezone.utc).isoformat(),
            "tool_outcome": {
                "cached": outcome.cached,
                "duration_ms": outcome.duration_ms,
                "output_chars": outcome.output_chars,
                "model_output_chars": outcome.model_output_chars,
                "output_truncated": outcome.output_truncated,
                "rejection_reason": outcome.rejection_reason,
                "timeout_reason": outcome.timeout_reason,
            },
        },
        id=uuid.uuid4().hex,
    )


async def _emit_outcome(
    writer: StreamWriter,
    call: PreparedCall,
    outcome: ToolOutcome,
) -> None:
    await emit(writer, {
        "type": "tool_result",
        "toolCallId": call.call_id,
        "result": outcome.display_output,
        "cached": outcome.cached,
        "error": outcome.display_output.get("error"),
        "status": outcome.status,
        "durationMs": outcome.duration_ms,
        "outputChars": outcome.output_chars,
        "modelOutputChars": outcome.model_output_chars,
        "outputTruncated": outcome.output_truncated,
        "rejectionReason": outcome.rejection_reason,
        "timeoutReason": outcome.timeout_reason,
    })


def _validation_message(exc: ValidationError) -> str:
    fields = [".".join(str(part) for part in item["loc"]) for item in exc.errors()]
    return f"工具参数不符合 Schema：{', '.join(fields) or 'unknown'}"


async def _prepare_calls(
    calls: list[dict[str, Any]],
    *,
    worker: WorkerName,
    context: AgentRuntimeContext,
    writer: StreamWriter,
) -> tuple[list[PreparedCall], list[tuple[PreparedCall, ToolOutcome]]]:
    """先同步完成所有廉价检查；被拒绝的调用也会产生配对 ToolMessage。"""
    prepared: list[PreparedCall] = []
    rejected: list[tuple[PreparedCall, ToolOutcome]] = []
    state_patch_reserved = False

    for index, raw_call in enumerate(calls):
        call_id = str(raw_call.get("id") or uuid.uuid4().hex)
        name = str(raw_call.get("name") or "")
        policy = TOOL_REGISTRY.get(name, worker)
        placeholder_policy = policy or ToolPolicy(
            tool=TOOL_REGISTRY.tools_for(worker)[0],
            workers=frozenset({worker}),
            timeout_seconds=1,
        )
        raw_args = raw_call.get("args", {})
        call = PreparedCall(
            index=index,
            call_id=call_id,
            name=name,
            args=raw_args if isinstance(raw_args, dict) else {},
            policy=placeholder_policy,
        )

        outcome: ToolOutcome | None = None
        if policy is None:
            outcome = _rejected_outcome(
                "tool_not_allowed",
                f"Worker 不允许调用工具：{name or 'unknown'}",
            )
        elif index >= MAX_BATCH_TOOL_CALLS:
            outcome = _rejected_outcome(
                "batch_call_limit",
                f"每批最多执行 {MAX_BATCH_TOOL_CALLS} 个工具调用",
            )
        elif not isinstance(raw_args, dict):
            outcome = _rejected_outcome("invalid_arguments", "工具参数必须是对象")
        else:
            try:
                validated = policy.tool.get_input_schema().model_validate(raw_args)
                call = PreparedCall(
                    index=index,
                    call_id=call_id,
                    name=name,
                    args=validated.model_dump(),
                    policy=policy,
                )
            except ValidationError as exc:
                outcome = _rejected_outcome(
                    "schema_validation_failed",
                    _validation_message(exc),
                )

        if outcome is None and policy is not None:
            if policy.produces_state_patch and state_patch_reserved:
                outcome = _rejected_outcome(
                    "state_patch_conflict",
                    "同一批最多允许一个工具修改 Graph State",
                )
            else:
                quota_error = await context.tool_budget.reserve(
                    name,
                    per_tool_limit=policy.max_calls_per_turn,
                )
                if quota_error:
                    outcome = _rejected_outcome(
                        quota_error,
                        "本回合工具调用额度已用尽",
                    )
                elif (
                    policy.requires_confirmation
                    and call_id not in context.confirmed_tool_call_ids
                ):
                    outcome = _rejected_outcome(
                        "confirmation_required",
                        f"工具 {name} 需要用户确认",
                    )
                elif policy.produces_state_patch:
                    state_patch_reserved = True

        if outcome is None:
            prepared.append(call)
        else:
            rejected.append((call, outcome))
            await _emit_outcome(writer, call, outcome)

    return prepared, rejected


async def _invoke(
    call: PreparedCall,
    *,
    model_id: DeepSeekModelId,
    writer: StreamWriter,
    context: AgentRuntimeContext,
) -> ToolOutcome:
    """执行一个已验证调用，并统一处理缓存、超时、裁剪和 State Patch。"""
    policy = call.policy
    started = time.perf_counter()
    cached = False
    try:
        async with asyncio.timeout(policy.timeout_seconds):
            cache = context.tool_cache
            lookup = (
                await cache.get(call.name, call.args, model_id=model_id)
                if cache
                else None
            )
            if lookup and lookup.hit:
                output: Any = lookup.value
                cached = True
            else:
                async with context.tool_budget.semaphore:
                    if call.name == "deep_search":
                        output = await run_deep_search_workflow(
                            query=str(call.args.get("query", "")).strip(),
                            focus=str(call.args.get("focus", "")).strip(),
                            model_id=model_id,
                        )
                    else:
                        output = await policy.tool.ainvoke(call.args)

            if (
                call.name == "web_search"
                and isinstance(output, dict)
                and not output.get("error")
            ):
                output = {**output, "results": dedupe_sources([output])}
            if (
                not cached
                and cache
                and not (isinstance(output, dict) and output.get("error"))
            ):
                await cache.put(call.name, call.args, output, model_id=model_id)

        duration_ms = max(0, round((time.perf_counter() - started) * 1000))
        display_output, display_truncated = _bounded_display_output(
            output,
            policy.max_display_output_chars,
        )
        model_content, output_chars, model_truncated = _bounded_model_content(
            output,
            policy.max_model_output_chars,
        )
        has_error = isinstance(output, dict) and bool(output.get("error"))
        citations: list[SourceCitation] = []
        if policy.produces_state_patch and isinstance(output, dict) and not has_error:
            raw_citations = output.get("results", [])
            if isinstance(raw_citations, list):
                citations = raw_citations
        outcome = ToolOutcome(
            model_content=model_content,
            display_output=display_output,
            state_patch={"source_citations": citations}
            if policy.produces_state_patch and not has_error
            else None,
            cached=cached,
            status="error" if has_error else "success",
            duration_ms=duration_ms,
            output_chars=output_chars,
            model_output_chars=len(model_content),
            output_truncated=display_truncated or model_truncated,
        )
    except TimeoutError:
        duration_ms = max(0, round((time.perf_counter() - started) * 1000))
        outcome = _timeout_outcome(policy, duration_ms)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Tool %s failed", call.name)
        duration_ms = max(0, round((time.perf_counter() - started) * 1000))
        output = {"error": str(exc), "code": "tool_execution_failed"}
        model_content, output_chars, truncated = _bounded_model_content(
            output,
            policy.max_model_output_chars,
        )
        display_output, display_truncated = _bounded_display_output(
            output,
            policy.max_display_output_chars,
        )
        outcome = ToolOutcome(
            model_content=model_content,
            display_output=display_output,
            state_patch=None,
            cached=False,
            status="error",
            duration_ms=duration_ms,
            output_chars=output_chars,
            model_output_chars=len(model_content),
            output_truncated=truncated or display_truncated,
        )

    await _emit_outcome(writer, call, outcome)
    return outcome


async def _execute_batch(
    calls: list[dict[str, Any]],
    *,
    worker: WorkerName,
    model_id: DeepSeekModelId,
    context: AgentRuntimeContext,
    writer: StreamWriter,
) -> tuple[list[ToolMessage], dict[str, Any]]:
    """按策略选择串行或并行执行，最后恢复模型原始调用顺序。"""
    prepared, rejected = await _prepare_calls(
        calls,
        worker=worker,
        context=context,
        writer=writer,
    )
    outcomes: dict[int, tuple[PreparedCall, ToolOutcome]] = {
        call.index: (call, outcome) for call, outcome in rejected
    }

    async def run(call: PreparedCall) -> None:
        outcome = await _invoke(
            call,
            model_id=model_id,
            writer=writer,
            context=context,
        )
        outcomes[call.index] = (call, outcome)

    if any(
        not call.policy.concurrency_safe or call.policy.produces_state_patch
        for call in prepared
    ):
        for call in prepared:
            await run(call)
    else:
        await asyncio.gather(*(run(call) for call in prepared))

    state_patch: dict[str, Any] = {}
    ordered = [outcomes[index] for index in sorted(outcomes)]
    for _call, outcome in ordered:
        if outcome.state_patch:
            state_patch.update(outcome.state_patch)
    return [_message(call, outcome) for call, outcome in ordered], state_patch


async def execute_tool_batch(
    state: AgentState,
    context: AgentRuntimeContext,
    writer: StreamWriter,
) -> dict[str, Any]:
    """Execute one Research Agent batch through the shared policy chain."""
    messages = state.get("messages", [])
    if not messages or not isinstance(messages[-1], AIMessage):
        return {"error": "工具阶段缺少 Agent 决策消息"}
    calls = messages[-1].tool_calls
    if not calls:
        return {"error": "工具阶段没有可执行的工具调用"}

    model_id: DeepSeekModelId = state.get(  # type: ignore[assignment]
        "model_id",
        "deepseek-v4-flash",
    )
    tool_messages, state_patch = await _execute_batch(
        calls,
        worker="research_agent",
        model_id=model_id,
        context=context,
        writer=writer,
    )
    return {
        "messages": tool_messages,
        "source_citations": state_patch.get("source_citations", []),
        "state_patch": state_patch or None,
    }


async def execute_general_tool_batch(
    calls: list[dict[str, Any]],
    *,
    model_id: DeepSeekModelId,
    context: AgentRuntimeContext,
    writer: StreamWriter,
) -> list[ToolMessage]:
    """Execute a General Agent batch through the same policy chain."""
    messages, _state_patch = await _execute_batch(
        calls,
        worker="general_agent",
        model_id=model_id,
        context=context,
        writer=writer,
    )
    return messages
