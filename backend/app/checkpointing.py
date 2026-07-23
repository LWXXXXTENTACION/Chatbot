"""LangGraph checkpoint 的持久化写穿透与 stream 级热读。

SQLite Saver 仍是 super-step、pending writes、父 checkpoint 与恢复位置的唯一真值。
本模块只对“当前 stream 读取最新 head”做 L1 read-through；指定 checkpoint、历史
列表和 delta history 全部绕过缓存，因此重启恢复与 time-travel 语义不会改变。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    DeltaChannelHistory,
)

from app.cache import BoundedTTLCache

CHECKPOINT_CACHE_TTL_SECONDS = 120.0
CHECKPOINT_CACHE_MAX_SCOPES = 2048

_ThreadKey = tuple[str, str]
_HotKey = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class _HotEntry:
    value: CheckpointTuple | None
    generation: int


def _thread_key(config: RunnableConfig) -> _ThreadKey | None:
    configurable = config.get("configurable", {})
    thread_id = configurable.get("thread_id")
    if not thread_id:
        return None
    return str(thread_id), str(configurable.get("checkpoint_ns", ""))


def _hot_key(config: RunnableConfig) -> _HotKey | None:
    """只有带 stream scope、且未指定 checkpoint_id 的最新 head 才可缓存。"""
    configurable = config.get("configurable", {})
    if configurable.get("checkpoint_id"):
        return None
    thread = _thread_key(config)
    scope = configurable.get("checkpoint_cache_scope")
    if thread is None or not scope:
        return None
    return str(scope), thread[0], thread[1]


def _observe_checkpoint(operation: str, source: str, duration_ms: float) -> None:
    from app.observability import current_trace

    collector = current_trace()
    if collector is not None:
        collector.observe_checkpoint(
            operation=operation,
            source=source,
            duration_ms=duration_ms,
        )


class CachedCheckpointSaver(BaseCheckpointSaver[Any]):
    """给持久化 Saver 增加不改变数据语义的 L1 最新状态缓存。"""

    def __init__(
        self,
        durable: BaseCheckpointSaver[Any],
        *,
        ttl_seconds: float = CHECKPOINT_CACHE_TTL_SECONDS,
        max_scopes: int = CHECKPOINT_CACHE_MAX_SCOPES,
    ) -> None:
        super().__init__(serde=durable.serde)
        self.durable = durable
        self._cache = BoundedTTLCache[_HotKey, _HotEntry](
            max_entries=max_scopes,
            default_ttl_seconds=ttl_seconds,
        )
        self._generations: dict[_ThreadKey, int] = {}
        self._key_locks: dict[_HotKey, asyncio.Lock] = {}
        self._key_locks_guard = asyncio.Lock()

    @property
    def config_specs(self) -> list[Any]:
        return list(self.durable.config_specs)

    async def _lock_for(self, key: _HotKey) -> asyncio.Lock:
        async with self._key_locks_guard:
            return self._key_locks.setdefault(key, asyncio.Lock())

    async def _invalidate_thread(self, key: _ThreadKey | None) -> None:
        if key is None:
            return
        self._generations[key] = self._generations.get(key, 0) + 1
        await self._cache.invalidate_where(
            lambda hot_key: hot_key[1] == key[0] and hot_key[2] == key[1]
        )

    async def aclear_scope(self, scope_id: str) -> None:
        """Graph 结束后主动清掉 stream 数据；TTL 只兜底异常退出。"""
        scope = str(scope_id)
        await self._cache.invalidate_where(lambda key: key[0] == scope)
        async with self._key_locks_guard:
            for key in [key for key in self._key_locks if key[0] == scope]:
                self._key_locks.pop(key, None)

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        started = time.perf_counter()
        hot_key = _hot_key(config)
        if hot_key is None:
            value = await self.durable.aget_tuple(config)
            _observe_checkpoint(
                "read", "durable", (time.perf_counter() - started) * 1000
            )
            return value

        thread_key = (hot_key[1], hot_key[2])
        generation = self._generations.get(thread_key, 0)
        cached = await self._cache.get_entry(hot_key)
        if cached is not None and cached.value.generation == generation:
            _observe_checkpoint("read", "hot", (time.perf_counter() - started) * 1000)
            return deepcopy(cached.value.value)

        # 同一 stream 的预检与 Graph 启动并发时，只允许一个任务落到 SQLite。
        lock = await self._lock_for(hot_key)
        async with lock:
            generation = self._generations.get(thread_key, 0)
            cached = await self._cache.get_entry(hot_key)
            if cached is not None and cached.value.generation == generation:
                _observe_checkpoint(
                    "read", "hot", (time.perf_counter() - started) * 1000
                )
                return deepcopy(cached.value.value)
            value = await self.durable.aget_tuple(config)
            # 缓存保存独立副本；调用方随后修改冷读对象也不会污染下一次热读。
            await self._cache.put(
                hot_key,
                _HotEntry(deepcopy(value), generation),
            )
        _observe_checkpoint("read", "durable", (time.perf_counter() - started) * 1000)
        return value

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        started = time.perf_counter()
        async for item in self.durable.alist(
            config,
            filter=filter,
            before=before,
            limit=limit,
        ):
            yield item
        _observe_checkpoint("history", "durable", (time.perf_counter() - started) * 1000)

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        started = time.perf_counter()
        result = await self.durable.aput(config, checkpoint, metadata, new_versions)
        await self._invalidate_thread(_thread_key(config))
        # SQLite Saver 返回的新 config 不认识自定义 scope；把它传给后续 super-step。
        scope = config.get("configurable", {}).get("checkpoint_cache_scope")
        if scope:
            result = {
                **result,
                "configurable": {
                    **result.get("configurable", {}),
                    "checkpoint_cache_scope": scope,
                },
            }
        _observe_checkpoint("write", "durable", (time.perf_counter() - started) * 1000)
        return result

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        started = time.perf_counter()
        await self.durable.aput_writes(config, writes, task_id, task_path)
        await self._invalidate_thread(_thread_key(config))
        _observe_checkpoint("write", "durable", (time.perf_counter() - started) * 1000)

    async def adelete_thread(self, thread_id: str) -> None:
        await self.durable.adelete_thread(thread_id)
        target = str(thread_id)
        for key in [key for key in self._generations if key[0] == target]:
            self._generations[key] += 1
        await self._cache.invalidate_where(lambda key: key[1] == target)

    async def adelete_for_runs(self, run_ids: Sequence[str]) -> None:
        await self.durable.adelete_for_runs(run_ids)
        for key in self._generations:
            self._generations[key] += 1
        await self._cache.clear()

    async def acopy_thread(self, source_thread_id: str, target_thread_id: str) -> None:
        await self.durable.acopy_thread(source_thread_id, target_thread_id)
        target = str(target_thread_id)
        await self._cache.invalidate_where(lambda key: key[1] == target)

    async def aprune(
        self,
        thread_ids: Sequence[str],
        *,
        strategy: str = "keep_latest",
    ) -> None:
        await self.durable.aprune(thread_ids, strategy=strategy)
        targets = {str(thread_id) for thread_id in thread_ids}
        await self._cache.invalidate_where(lambda key: key[1] in targets)

    async def aget_delta_channel_history(
        self,
        *,
        config: RunnableConfig,
        channels: Sequence[str],
    ) -> dict[str, DeltaChannelHistory]:
        return dict(await self.durable.aget_delta_channel_history(
            config=config,
            channels=channels,
        ))

    def get_next_version(self, current: Any | None, channel: None) -> Any:
        return self.durable.get_next_version(current, channel)
