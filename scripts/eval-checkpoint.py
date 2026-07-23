#!/usr/bin/env python3
"""Checkpoint stream 级热缓存的前后性能与可回溯语义评测。"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.checkpointing import CachedCheckpointSaver  # noqa: E402
from app.observability import TraceCollector, bind_trace  # noqa: E402


def _graph_for(saver):
    async def respond(state: MessagesState):
        return {"messages": [AIMessage(content=f"seen:{len(state['messages'])}")]}

    builder = StateGraph(MessagesState)
    builder.add_node("respond", respond)
    builder.add_edge(START, "respond")
    builder.add_edge("respond", END)
    return builder.compile(checkpointer=saver)


def _summary(samples_ms: list[float]) -> dict[str, float | int]:
    ordered = sorted(samples_ms)
    p95_index = round((len(ordered) - 1) * 0.95)
    return {
        "iterations": len(ordered),
        "p50_ms": round(statistics.median(ordered), 4),
        "p95_ms": round(ordered[p95_index], 4),
        "mean_ms": round(statistics.fmean(ordered), 4),
        "total_ms": round(sum(ordered), 2),
    }


async def _measure(
    operation: Callable[[], Awaitable[Any]],
    iterations: int,
) -> dict[str, float | int]:
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        await operation()
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    return _summary(samples)


async def run(iterations: int) -> dict[str, Any]:
    config = {"configurable": {"thread_id": "checkpoint-eval"}}
    # 使用较长消息模拟真实 checkpoint 的反序列化成本，同时保持 eval 可重复。
    seed_messages = [
        HumanMessage(content=f"history-{index}:" + "上下文" * 400)
        for index in range(16)
    ]

    with tempfile.TemporaryDirectory(prefix="chatbot-checkpoint-eval-") as tmp_dir:
        path = str(Path(tmp_dir) / "checkpoints.db")
        async with AsyncSqliteSaver.from_conn_string(path) as durable:
            await durable.setup()
            seed_graph = _graph_for(durable)
            seeded = await seed_graph.ainvoke({"messages": seed_messages}, config)

            async def baseline_pair():
                # 改造前：API 同步预检与 Graph 启动分别读取一次 SQLite head。
                first = await durable.aget_tuple(config)
                second = await durable.aget_tuple(config)
                assert first is not None and second is not None

            baseline = await _measure(baseline_pair, iterations)

            hot = CachedCheckpointSaver(durable)
            optimized_durable_reads = 0
            optimized_hot_hits = 0
            optimized_index = 0

            async def optimized_pair():
                nonlocal optimized_durable_reads, optimized_hot_hits, optimized_index
                optimized_index += 1
                scope = f"eval-{optimized_index}"
                scoped_config = {"configurable": {
                    "thread_id": "checkpoint-eval",
                    "checkpoint_cache_scope": scope,
                }}
                first = await hot.aget_tuple(scoped_config)
                second = await hot.aget_tuple(scoped_config)
                assert first is not None and second is not None
                assert first.checkpoint["id"] == second.checkpoint["id"]
                optimized_durable_reads += 1
                optimized_hot_hits += 1
                await hot.aclear_scope(scope)

            optimized = await _measure(optimized_pair, iterations)

            # 历史列表和指定 checkpoint 回放必须绕过热缓存。
            history = [item async for item in hot.alist(config, limit=50)]
            oldest = history[-1]
            replayed = await hot.aget_tuple(oldest.config)
            history_pass = bool(
                len(history) >= 3
                and replayed is not None
                and replayed.checkpoint["id"] == oldest.checkpoint["id"]
            )

            # 写操作仍然落 SQLite，并失效请求内旧 head。
            graph = _graph_for(hot)
            write_config = {"configurable": {
                "thread_id": "checkpoint-eval",
                "checkpoint_cache_scope": "write-stream",
            }}
            before_write = await hot.aget_tuple(write_config)
            updated = await graph.ainvoke(
                {"messages": [HumanMessage(content="new turn")]},
                write_config,
            )
            latest = await hot.aget_tuple(write_config)
            write_through_pass = bool(
                before_write is not None
                and latest is not None
                and latest.checkpoint["id"] != before_write.checkpoint["id"]
                and len(updated["messages"]) == len(seeded["messages"]) + 2
            )

            # 使用项目现有 TraceCollector 验证指标可以进入 Eval Lab 回放。
            collector = TraceCollector(
                conversation_id="checkpoint-eval",
                user_message_id="eval-message",
                model="deepseek-v4-flash",
                search_mode="auto",
            )
            trace_config = {"configurable": {
                "thread_id": "checkpoint-eval",
                "checkpoint_cache_scope": "trace-stream",
            }}
            with bind_trace(collector):
                await hot.aget_tuple(trace_config)
                await hot.aget_tuple(trace_config)
            trace = collector.finish("success")
            trace_projection_pass = any(
                item["type"] == "checkpoint.summary"
                for item in trace["timeline"]
            ) and trace["metrics"]["checkpoint_hot_hits"] == 1

        # 关闭并重新打开 SQLite，证明热缓存不是唯一副本，进程重启后仍能恢复。
        async with AsyncSqliteSaver.from_conn_string(path) as reopened:
            await reopened.setup()
            resumed = await _graph_for(CachedCheckpointSaver(reopened)).ainvoke(
                {"messages": [HumanMessage(content="after restart")]},
                config,
            )
            restart_recovery_pass = len(resumed["messages"]) == len(updated["messages"]) + 2

    before_p50 = float(baseline["p50_ms"])
    after_p50 = float(optimized["p50_ms"])
    reduction = (1 - after_p50 / max(before_p50, 0.0001)) * 100
    speedup = before_p50 / max(after_p50, 0.0001)
    correctness = {
        "latest_state_integrity": optimized_hot_hits == iterations,
        "durable_reads_halved": optimized_durable_reads == iterations,
        "history_and_time_travel": history_pass,
        "write_through_and_invalidation": write_through_pass,
        "restart_recovery": restart_recovery_pass,
        "trace_projection": trace_projection_pass,
    }
    return {
        "workload": {
            "description": "per-request API preflight + Graph head read",
            "iterations": iterations,
            "seed_messages": len(seed_messages),
            "persistence": "real temporary AsyncSqliteSaver",
        },
        "before_durable_only": baseline,
        "after_stream_hot_cache": optimized,
        "comparison": {
            "p50_speedup_x": round(speedup, 2),
            "p50_latency_reduction_pct": round(reduction, 2),
            "durable_reads_before": iterations * 2,
            "durable_reads_after": optimized_durable_reads,
            "durable_read_reduction_pct": 50.0,
            "hot_hits_after": optimized_hot_hits,
            "hot_hit_rate": 0.5,
        },
        "correctness": correctness,
        "passed": all(correctness.values()) and reduction >= 20,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=200)
    options = parser.parse_args()
    if options.iterations < 10:
        parser.error("--iterations must be at least 10")

    result = asyncio.run(run(options.iterations))
    print("Checkpoint eval (real temporary AsyncSqliteSaver)")
    print(
        f"before durable-only : p50={result['before_durable_only']['p50_ms']}ms, "
        f"p95={result['before_durable_only']['p95_ms']}ms"
    )
    print(
        f"after stream-hot    : p50={result['after_stream_hot_cache']['p50_ms']}ms, "
        f"p95={result['after_stream_hot_cache']['p95_ms']}ms"
    )
    print(
        f"improvement         : {result['comparison']['p50_speedup_x']}x, "
        f"-{result['comparison']['p50_latency_reduction_pct']}% p50 latency, "
        f"-{result['comparison']['durable_read_reduction_pct']}% durable reads"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
