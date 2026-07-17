"""Persistence and aggregation for JSON run traces stored as message parts."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.database.models import Conversation, Message, MessagePart


TRACE_PART_TYPE = "trace"


async def persist_run_trace(
    db: AsyncSession,
    message_id: str,
    trace: dict[str, Any],
) -> None:
    """Attach an idempotent trace part to the originating user message."""
    run_id = str(trace["run_id"])
    result = await db.execute(select(MessagePart).where(MessagePart.id == run_id))
    part = result.scalar_one_or_none()
    if part is None:
        part = MessagePart(
            id=run_id,
            message_id=message_id,
            type=TRACE_PART_TYPE,
            tool_output=trace,
            position=10_000,
        )
        db.add(part)
    else:
        part.tool_output = trace
        flag_modified(part, "tool_output")
    await db.commit()


async def list_run_traces(
    db: AsyncSession,
    user_id: str,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Read recent traces scoped through conversation ownership."""
    result = await db.execute(
        select(MessagePart, Message, Conversation)
        .join(Message, MessagePart.message_id == Message.id)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(
            MessagePart.type == TRACE_PART_TYPE,
            Conversation.user_id == user_id,
        )
        .order_by(desc(Message.created_at))
        .limit(max(1, min(limit, 500)))
    )
    traces: list[dict[str, Any]] = []
    for part, message, conversation in result.all():
        if not isinstance(part.tool_output, dict):
            continue
        trace = deepcopy(part.tool_output)
        trace["conversation"] = {
            "id": conversation.id,
            "title": conversation.title,
        }
        trace.setdefault("started_at", message.created_at.isoformat())
        traces.append(trace)
    return traces


async def update_run_evaluation(
    db: AsyncSession,
    user_id: str,
    run_id: str,
    *,
    passed: bool | None,
    note: str,
    case_id: str,
) -> dict[str, Any] | None:
    result = await db.execute(
        select(MessagePart, Conversation)
        .join(Message, MessagePart.message_id == Message.id)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(
            MessagePart.id == run_id,
            MessagePart.type == TRACE_PART_TYPE,
            Conversation.user_id == user_id,
        )
    )
    row = result.one_or_none()
    if row is None:
        return None
    part, conversation = row
    trace = deepcopy(part.tool_output) if isinstance(part.tool_output, dict) else {}
    trace["evaluation"] = (
        {
            "passed": passed,
            "note": note.strip()[:1000],
            "case_id": case_id.strip()[:128],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if passed is not None
        else None
    )
    part.tool_output = trace
    flag_modified(part, "tool_output")
    await db.commit()
    trace["conversation"] = {"id": conversation.id, "title": conversation.title}
    return trace


def _number(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def aggregate_versions(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate version-level cost, quality, and execution metrics."""
    groups: dict[str, dict[str, Any]] = {}
    category_groups: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"runs": 0, "total_tokens": 0})
    )

    for trace in traces:
        version = trace.get("version") if isinstance(trace.get("version"), dict) else {}
        version_id = str(version.get("id") or "unknown")
        metrics = trace.get("metrics") if isinstance(trace.get("metrics"), dict) else {}
        evaluation = (
            trace.get("evaluation")
            if isinstance(trace.get("evaluation"), dict)
            else None
        )
        started_at = str(trace.get("started_at") or "")
        group = groups.setdefault(version_id, {
            "id": version_id,
            "label": str(version.get("label") or "未标记版本"),
            "model": str(trace.get("model") or "unknown"),
            "code_fingerprint": str(version.get("code_fingerprint") or ""),
            "first_seen": started_at,
            "last_seen": started_at,
            "runs": 0,
            "successful_runs": 0,
            "failed_runs": 0,
            "total_tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "llm_calls": 0,
            "tool_calls": 0,
            "tool_errors": 0,
            "tool_rejections": 0,
            "tool_timeouts": 0,
            "tool_duration_ms": 0,
            "tool_output_chars": 0,
            "tool_truncations": 0,
            "duration_ms": 0,
            "evaluated_runs": 0,
            "passed_runs": 0,
        })
        group["runs"] += 1
        group["successful_runs"] += int(trace.get("status") == "success")
        group["failed_runs"] += int(trace.get("status") != "success")
        group["total_tokens"] += _number(metrics.get("total_tokens"))
        group["input_tokens"] += _number(metrics.get("input_tokens"))
        group["output_tokens"] += _number(metrics.get("output_tokens"))
        group["llm_calls"] += _number(metrics.get("llm_calls"))
        group["tool_calls"] += _number(metrics.get("tool_calls"))
        group["tool_errors"] += _number(metrics.get("tool_errors"))
        group["tool_rejections"] += _number(metrics.get("tool_rejections"))
        group["tool_timeouts"] += _number(metrics.get("tool_timeouts"))
        group["tool_duration_ms"] += _number(metrics.get("tool_duration_ms"))
        group["tool_output_chars"] += _number(metrics.get("tool_output_chars"))
        group["tool_truncations"] += _number(metrics.get("tool_truncations"))
        group["duration_ms"] += _number(trace.get("duration_ms"))
        if evaluation is not None and isinstance(evaluation.get("passed"), bool):
            group["evaluated_runs"] += 1
            group["passed_runs"] += int(evaluation["passed"])
        if started_at:
            group["first_seen"] = min(group["first_seen"] or started_at, started_at)
            group["last_seen"] = max(group["last_seen"] or started_at, started_at)

        category = str(trace.get("search_mode") or "auto")
        category_groups[version_id][category]["runs"] += 1
        category_groups[version_id][category]["total_tokens"] += _number(
            metrics.get("total_tokens")
        )

    category_labels = {"auto": "普通对话", "web": "联网检索", "deep": "深度检索"}
    result: list[dict[str, Any]] = []
    for version_id, group in groups.items():
        runs = group["runs"]
        evaluated = group["evaluated_runs"]
        group["avg_tokens"] = round(group["total_tokens"] / runs) if runs else 0
        group["avg_duration_ms"] = round(group.pop("duration_ms") / runs) if runs else 0
        group["avg_tool_duration_ms"] = (
            round(group["tool_duration_ms"] / group["tool_calls"])
            if group["tool_calls"]
            else 0
        )
        group["avg_tool_output_chars"] = (
            round(group["tool_output_chars"] / group["tool_calls"])
            if group["tool_calls"]
            else 0
        )
        group["success_rate"] = round(group["successful_runs"] / runs, 4) if runs else 0
        group["pass_rate"] = (
            round(group["passed_runs"] / evaluated, 4) if evaluated else None
        )
        group["categories"] = [
            {
                "id": category,
                "label": category_labels.get(category, category),
                "runs": values["runs"],
                "total_tokens": values["total_tokens"],
                "avg_tokens": round(values["total_tokens"] / values["runs"]),
            }
            for category, values in sorted(category_groups[version_id].items())
        ]
        result.append(group)
    return sorted(result, key=lambda item: item["last_seen"], reverse=True)
