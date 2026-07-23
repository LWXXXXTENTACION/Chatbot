"""面向公开、幂等工具的 L1/L2/L3 精确缓存。

读取顺序固定为 ``进程内 L1 → Redis L2 → 数据库 L3``。下层命中后会把仍在
有效期内的值逐级回填到上层；真实工具执行成功后则并行写入三层。任何缓存层
故障都按 miss/fail-open 处理，不能让可选加速能力成为聊天主链的单点故障。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Generic, Literal, TypeVar

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import ToolCacheEntry

logger = logging.getLogger("chatbot.cache")

CACHE_PREFIX = "chatbot:tool"
CACHE_VERSION = "v2"
REDIS_RETRY_SECONDS = 30.0
DEFAULT_L1_MAX_ENTRIES = 1024
L3_IO_TIMEOUT_SECONDS = 1.0

CacheLayer = Literal["l1", "l2", "l3"]
K = TypeVar("K")
V = TypeVar("V")


@dataclass(frozen=True, slots=True)
class TTLCacheEntry(Generic[V]):
    """显式区分“没有 key”和“缓存值恰好为 None”。"""

    value: V


@dataclass(frozen=True, slots=True)
class _TTLValue(Generic[V]):
    value: V
    expires_at: float


class BoundedTTLCache(Generic[K, V]):
    """异步安全的有界 TTL/LRU，供不同热路径复用。

    这个类只提供进程内性能加速，不承担任何持久化语义。每次读都会推进 LRU，
    写满后淘汰最久未访问项；TTL 使用 monotonic clock，不受系统时间调整影响。
    """

    def __init__(self, *, max_entries: int, default_ttl_seconds: float) -> None:
        self.max_entries = max(0, max_entries)
        self.default_ttl_seconds = max(0.0, default_ttl_seconds)
        self._values: OrderedDict[K, _TTLValue[V]] = OrderedDict()
        self._lock = asyncio.Lock()

    @property
    def size(self) -> int:
        return len(self._values)

    async def get_entry(self, key: K) -> TTLCacheEntry[V] | None:
        if self.max_entries <= 0:
            return None
        now = time.monotonic()
        async with self._lock:
            cached = self._values.get(key)
            if cached is None:
                return None
            if cached.expires_at <= now:
                self._values.pop(key, None)
                return None
            self._values.move_to_end(key)
            return TTLCacheEntry(cached.value)

    async def put(
        self,
        key: K,
        value: V,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        ttl = self.default_ttl_seconds if ttl_seconds is None else max(0.0, ttl_seconds)
        if self.max_entries <= 0 or ttl <= 0:
            return
        async with self._lock:
            self._values[key] = _TTLValue(value, time.monotonic() + ttl)
            self._values.move_to_end(key)
            while len(self._values) > self.max_entries:
                self._values.popitem(last=False)

    async def invalidate(self, key: K) -> None:
        async with self._lock:
            self._values.pop(key, None)

    async def invalidate_where(self, predicate: Callable[[K], bool]) -> None:
        async with self._lock:
            for key in [key for key in self._values if predicate(key)]:
                self._values.pop(key, None)

    async def clear(self) -> None:
        async with self._lock:
            self._values.clear()


@dataclass(frozen=True)
class CachePolicy:
    ttl_seconds: int


@dataclass(frozen=True)
class CacheLookup:
    """一次缓存查询的结果；``layer`` 用于事件追踪和性能评测。"""

    hit: bool
    value: Any = None
    layer: CacheLayer | None = None


@dataclass(frozen=True)
class _CacheValue:
    """三层共享的内部值结构，统一使用绝对过期时间避免回填后 TTL 重置。"""

    value: Any
    expires_at: float


CACHE_POLICIES: dict[str, CachePolicy] = {
    # 不同结果的时效性不同，TTL 必须按工具语义设置，不能使用一个全局过期时间。
    "web_search": CachePolicy(ttl_seconds=300),
    "deep_search": CachePolicy(ttl_seconds=600),
    "get_weather": CachePolicy(ttl_seconds=60),
    "calculate": CachePolicy(ttl_seconds=24 * 60 * 60),
}


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize(item) for item in value]
    if isinstance(value, str):
        return " ".join(value.strip().split())
    return value


def tool_cache_key(
    tool_name: str,
    args: dict[str, Any],
    *,
    model_id: str = "",
) -> str:
    """从规范化参数生成稳定、带版本的 key。

    dict 排序、字符串空白和大小写先归一化，再计算 SHA-256；Deep Search 还包含
    model_id，因为不同模型可能生成不同研究简报。key 中不直接暴露用户原始查询。
    """
    if tool_name == "deep_search":
        effective_args = {
            "query": args.get("query", ""),
            "focus": args.get("focus", ""),
        }
    elif tool_name == "web_search":
        effective_args = {
            "query": args.get("query", ""),
            "max_results": args.get("max_results", 5),
        }
    elif tool_name == "get_weather":
        city = str(args.get("city", ""))
        effective_args = {"city": " ".join(city.strip().split()).casefold()}
    elif tool_name == "calculate":
        expression = str(args.get("expression", ""))
        effective_args = {"expression": "".join(expression.split())}
    else:
        effective_args = dict(args)
    payload: dict[str, Any] = {"args": _normalize(effective_args)}
    if tool_name == "deep_search":
        payload["model_id"] = model_id
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"{CACHE_PREFIX}:{tool_name}:{CACHE_VERSION}:{digest}"


def _datetime_to_timestamp(value: datetime) -> float:
    """兼容 SQLite 读回的无时区 datetime，并统一按 UTC 解释。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


class MultiLayerCache:
    """L1 内存 + L2 Redis + L3 数据库的读穿透、写穿透缓存。

    L1 使用有界 LRU，防止长进程无限增长；Redis 与数据库都是可选依赖，但生产
    装配会始终提供数据库层。所有层保存同一个绝对 ``expires_at``，因此从 L3
    回填到 L2/L1 时只使用剩余 TTL，不会意外延长旧数据寿命。
    """

    def __init__(
        self,
        redis_client: Any | None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        *,
        l1_max_entries: int = DEFAULT_L1_MAX_ENTRIES,
    ) -> None:
        self.redis = redis_client
        self.session_factory = session_factory
        self.l1_max_entries = max(0, l1_max_entries)
        self._l1 = BoundedTTLCache[str, _CacheValue](
            max_entries=self.l1_max_entries,
            default_ttl_seconds=max(
                policy.ttl_seconds for policy in CACHE_POLICIES.values()
            ),
        )
        self._redis_retry_at = 0.0

    @property
    def enabled(self) -> bool:
        return bool(
            self.l1_max_entries > 0
            or self.redis is not None
            or self.session_factory is not None
        )

    @property
    def l1_size(self) -> int:
        """仅供运行观测与 eval 使用；缓存读写仍必须经过锁保护的方法。"""
        return self._l1.size

    async def get(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        model_id: str = "",
    ) -> CacheLookup:
        policy = CACHE_POLICIES.get(tool_name)
        if policy is None or not self.enabled:
            return CacheLookup(hit=False)

        key = tool_cache_key(tool_name, args, model_id=model_id)
        now = time.time()

        # 1. L1 是当前进程热路径，不发生序列化和网络/磁盘 I/O。
        cached = await self._get_l1(key, now)
        if cached is not None:
            return CacheLookup(hit=True, value=cached.value, layer="l1")

        # 2. L2 允许不同进程共享热结果；命中后立即晋升到 L1。
        cached = await self._get_l2(key, now)
        if cached is not None:
            await self._put_l1(key, cached)
            return CacheLookup(hit=True, value=cached.value, layer="l2")

        # 3. L3 跨 Redis 重启保留结果；命中后同时回填 L1 与 L2。
        cached = await self._get_l3(key, now)
        if cached is not None:
            await asyncio.gather(
                self._put_l1(key, cached),
                self._put_l2(key, cached),
            )
            return CacheLookup(hit=True, value=cached.value, layer="l3")

        return CacheLookup(hit=False)

    async def put(
        self,
        tool_name: str,
        args: dict[str, Any],
        value: Any,
        *,
        model_id: str = "",
    ) -> None:
        policy = CACHE_POLICIES.get(tool_name)
        if policy is None or not self.enabled:
            return
        if isinstance(value, dict) and value.get("error"):
            # 错误结果不能缓存，否则一次临时故障会在整个 TTL 内持续污染回答。
            return

        key = tool_cache_key(tool_name, args, model_id=model_id)
        cached = _CacheValue(
            value=value,
            expires_at=time.time() + policy.ttl_seconds,
        )

        # L1 先完成，L2/L3 并行写；任一外部层故障都不会抹掉已得到的工具结果。
        await self._put_l1(key, cached)
        await asyncio.gather(
            self._put_l2(key, cached),
            self._put_l3(key, tool_name, cached),
        )

    async def clear_l1(self) -> None:
        """清空当前进程热缓存，主要用于测试重启/跨层回填。"""
        await self._l1.clear()

    async def _get_l1(self, key: str, now: float) -> _CacheValue | None:
        if self.l1_max_entries <= 0:
            return None
        entry = await self._l1.get_entry(key)
        if entry is None:
            return None
        cached = entry.value
        if cached.expires_at <= now:
            await self._l1.invalidate(key)
            return None
        return cached

    async def _put_l1(self, key: str, cached: _CacheValue) -> None:
        if self.l1_max_entries <= 0 or cached.expires_at <= time.time():
            return
        await self._l1.put(
            key,
            cached,
            ttl_seconds=cached.expires_at - time.time(),
        )

    async def _get_l2(self, key: str, now: float) -> _CacheValue | None:
        if self.redis is None or time.monotonic() < self._redis_retry_at:
            return None
        try:
            raw = await self.redis.get(key)
        except Exception as exc:
            self._mark_redis_unavailable(exc)
            return None
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            envelope = json.loads(raw)
            if envelope.get("version") != CACHE_VERSION:
                return None
            cached = _CacheValue(
                value=envelope["value"],
                expires_at=float(envelope["expires_at"]),
            )
            return cached if cached.expires_at > now else None
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Ignoring malformed Redis tool cache entry %s: %s", key, exc)
            return None

    async def _put_l2(self, key: str, cached: _CacheValue) -> None:
        if self.redis is None or time.monotonic() < self._redis_retry_at:
            return
        remaining_ttl = math.ceil(cached.expires_at - time.time())
        if remaining_ttl <= 0:
            return
        envelope = json.dumps(
            {
                "version": CACHE_VERSION,
                "value": cached.value,
                "expires_at": cached.expires_at,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            await self.redis.set(key, envelope, ex=remaining_ttl)
        except Exception as exc:
            self._mark_redis_unavailable(exc)

    async def _get_l3(self, key: str, now: float) -> _CacheValue | None:
        if self.session_factory is None:
            return None
        try:
            async with asyncio.timeout(L3_IO_TIMEOUT_SECONDS):
                async with self.session_factory() as db:
                    row = await db.get(ToolCacheEntry, key)
                    if row is None:
                        return None
                    expires_at = _datetime_to_timestamp(row.expires_at)
                    if expires_at <= now:
                        # 过期项按 miss 处理，并顺手清理本次已经访问到的冷数据。
                        await db.delete(row)
                        await db.commit()
                        return None
                    return _CacheValue(value=row.value, expires_at=expires_at)
        except Exception as exc:
            logger.warning("Database L3 tool cache read failed; treating as miss: %s", exc)
            return None

    async def _put_l3(
        self,
        key: str,
        tool_name: str,
        cached: _CacheValue,
    ) -> None:
        if self.session_factory is None or cached.expires_at <= time.time():
            return
        expires_at = datetime.fromtimestamp(cached.expires_at, tz=timezone.utc)
        try:
            async with asyncio.timeout(L3_IO_TIMEOUT_SECONDS):
                async with self.session_factory() as db:
                    row = await db.get(ToolCacheEntry, key)
                    if row is None:
                        db.add(ToolCacheEntry(
                            cache_key=key,
                            tool_name=tool_name,
                            value=cached.value,
                            expires_at=expires_at,
                        ))
                    else:
                        row.tool_name = tool_name
                        row.value = cached.value
                        row.expires_at = expires_at
                        row.updated_at = datetime.now(timezone.utc)
                    try:
                        await db.commit()
                    except IntegrityError:
                        # 两个请求可能同时写同一个新 key；失败的一方回滚后更新胜者。
                        await db.rollback()
                        winner = await db.get(ToolCacheEntry, key)
                        if winner is None:
                            raise
                        winner.tool_name = tool_name
                        winner.value = cached.value
                        winner.expires_at = expires_at
                        winner.updated_at = datetime.now(timezone.utc)
                        await db.commit()
        except Exception as exc:
            logger.warning("Database L3 tool cache write failed; continuing: %s", exc)

    def _mark_redis_unavailable(self, exc: Exception) -> None:
        now = time.monotonic()
        if now >= self._redis_retry_at:
            logger.warning(
                "Redis L2 tool cache unavailable; skipping it for %.0fs: %s",
                REDIS_RETRY_SECONDS,
                exc,
            )
        self._redis_retry_at = now + REDIS_RETRY_SECONDS


# 兼容旧导入名；新代码应优先使用能表达真实结构的 MultiLayerCache。
ToolCache = MultiLayerCache


async def create_redis_client(url: str, *, enabled: bool) -> Any | None:
    """创建异步 Redis 客户端；启动探测失败只告警，不阻止 FastAPI 启动。"""
    if not enabled:
        return None
    from redis.asyncio import Redis

    client = Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=0.5,
        socket_timeout=0.5,
        health_check_interval=30,
    )
    try:
        await client.ping()
        logger.info("Redis L2 cache and distributed rate limiting enabled")
    except Exception as exc:
        logger.warning("Redis unavailable at startup; fail-open mode enabled: %s", exc)
    return client
