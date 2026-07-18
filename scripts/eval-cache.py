#!/usr/bin/env python3
"""三层缓存的可重复微基准与语义检查。

基线精确复现改造前“每次命中都访问 Redis 并 JSON 解码”的热读路径；优化版使用
同一份数据和请求，但首次 L2 命中后晋升到 L1。为了让结果不依赖本机是否安装
Redis，eval 使用固定 1ms RTT 的内存 Redis 替身；L3 则使用真实临时 SQLite。
"""

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

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.cache import CACHE_VERSION, MultiLayerCache, tool_cache_key  # noqa: E402
from app.database.models import Base  # noqa: E402


class DelayedRedis:
    """带固定网络往返延迟的 Redis 测试替身，保证跨机器结果可比较。"""

    def __init__(self, latency_seconds: float = 0.001) -> None:
        self.latency_seconds = latency_seconds
        self.values: dict[str, str] = {}
        self.get_calls = 0
        self.set_calls = 0
        self.fail = False

    async def get(self, key: str) -> str | None:
        await asyncio.sleep(self.latency_seconds)
        self.get_calls += 1
        if self.fail:
            raise ConnectionError("simulated Redis outage")
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int) -> None:
        del ex
        await asyncio.sleep(self.latency_seconds)
        self.set_calls += 1
        if self.fail:
            raise ConnectionError("simulated Redis outage")
        self.values[key] = value


class RedisOnlyBaseline:
    """改造前的 Redis-only 热读逻辑，仅用于 before 指标。"""

    def __init__(self, redis: DelayedRedis) -> None:
        self.redis = redis

    async def get(self, key: str) -> Any:
        raw = await self.redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)["value"]


async def _measure(
    operation: Callable[[], Awaitable[Any]],
    iterations: int,
) -> dict[str, float | int]:
    samples_ms: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        await operation()
        samples_ms.append((time.perf_counter_ns() - started) / 1_000_000)
    ordered = sorted(samples_ms)
    p95_index = round((len(ordered) - 1) * 0.95)
    return {
        "iterations": iterations,
        "p50_ms": round(statistics.median(ordered), 4),
        "p95_ms": round(ordered[p95_index], 4),
        "mean_ms": round(statistics.fmean(ordered), 4),
        "total_ms": round(sum(ordered), 2),
    }


async def run(iterations: int) -> dict[str, Any]:
    args = {"city": "上海"}
    value = {"temperature": 28, "condition": "晴"}
    key = tool_cache_key("get_weather", args)
    expires_at = time.time() + 60

    redis = DelayedRedis(latency_seconds=0.001)
    redis.values[key] = json.dumps({
        "version": CACHE_VERSION,
        "value": value,
        "expires_at": expires_at,
    })

    baseline = RedisOnlyBaseline(redis)
    before = await _measure(lambda: baseline.get(key), iterations)

    optimized = MultiLayerCache(redis)
    promotion_started = time.perf_counter_ns()
    first_l2 = await optimized.get("get_weather", args)
    l2_promotion_ms = (time.perf_counter_ns() - promotion_started) / 1_000_000
    after = await _measure(lambda: optimized.get("get_weather", args), iterations)
    second_l1 = await optimized.get("get_weather", args)

    with tempfile.TemporaryDirectory(prefix="chatbot-cache-eval-") as tmp_dir:
        database_path = Path(tmp_dir) / "cache.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        # 先只写 L3，再使用一个全新的 Redis/L1 实例读取，模拟服务重启后的冷启动。
        l3_writer = MultiLayerCache(None, sessions, l1_max_entries=0)
        l3_write_started = time.perf_counter_ns()
        await l3_writer.put("get_weather", args, value)
        l3_write_ms = (time.perf_counter_ns() - l3_write_started) / 1_000_000
        recovery_redis = DelayedRedis(latency_seconds=0)
        l3_reader = MultiLayerCache(recovery_redis, sessions)
        l3_recovery_started = time.perf_counter_ns()
        first_l3 = await l3_reader.get("get_weather", args)
        l3_recovery_ms = (time.perf_counter_ns() - l3_recovery_started) / 1_000_000
        promoted_l1 = await l3_reader.get("get_weather", args)

        # L2 故障时必须继续访问 L3，而不是直接把整条链路判定为 miss。
        failing_redis = DelayedRedis(latency_seconds=0)
        failing_redis.fail = True
        fallback_reader = MultiLayerCache(failing_redis, sessions)
        fallback_l3 = await fallback_reader.get("get_weather", args)
        await engine.dispose()

    before_p50 = float(before["p50_ms"])
    after_p50 = float(after["p50_ms"])
    speedup = before_p50 / max(after_p50, 0.0001)
    reduction = (1 - after_p50 / max(before_p50, 0.0001)) * 100
    correctness = {
        "l2_to_l1_promotion": first_l2.layer == "l2" and second_l1.layer == "l1",
        "l3_to_l2_l1_backfill": (
            first_l3.layer == "l3"
            and promoted_l1.layer == "l1"
            and recovery_redis.set_calls == 1
        ),
        "redis_failure_falls_back_to_l3": fallback_l3.layer == "l3",
        "value_integrity": (
            first_l2.value == value
            and first_l3.value == value
            and fallback_l3.value == value
        ),
    }
    return {
        "workload": {
            "description": "sequential warm reads of one normalized tool result",
            "redis_rtt_ms": 1,
            "iterations": iterations,
            "l3_backend": "temporary SQLite via SQLAlchemy async",
        },
        "before_redis_only": before,
        "after_l1_hot_path": after,
        "comparison": {
            "p50_speedup_x": round(speedup, 2),
            "p50_latency_reduction_pct": round(reduction, 2),
            "redis_gets_before": iterations,
            "redis_gets_after_promotion": 0,
        },
        "cold_path_observation": {
            "l2_hit_and_l1_promotion_ms": round(l2_promotion_ms, 4),
            "l3_sqlite_write_ms": round(l3_write_ms, 4),
            "l3_hit_and_upper_backfill_ms": round(l3_recovery_ms, 4),
            "note": "observations only; cold-path cost depends on real infrastructure",
        },
        "correctness": correctness,
        "passed": all(correctness.values()) and speedup >= 2,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=300)
    options = parser.parse_args()
    if options.iterations < 10:
        parser.error("--iterations must be at least 10")

    result = asyncio.run(run(options.iterations))
    print("Cache eval (controlled Redis RTT = 1ms)")
    print(
        f"before Redis-only : p50={result['before_redis_only']['p50_ms']}ms, "
        f"p95={result['before_redis_only']['p95_ms']}ms"
    )
    print(
        f"after L1 hot path : p50={result['after_l1_hot_path']['p50_ms']}ms, "
        f"p95={result['after_l1_hot_path']['p95_ms']}ms"
    )
    print(
        f"improvement       : {result['comparison']['p50_speedup_x']}x, "
        f"-{result['comparison']['p50_latency_reduction_pct']}% p50 latency"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
