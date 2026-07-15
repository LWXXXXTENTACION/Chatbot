"""Fail-open Redis-backed exact cache for public, idempotent tools."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("chatbot.cache")

CACHE_PREFIX = "chatbot:tool"
CACHE_VERSION = "v1"
REDIS_RETRY_SECONDS = 30.0


@dataclass(frozen=True)
class CachePolicy:
    ttl_seconds: int


@dataclass(frozen=True)
class CacheLookup:
    hit: bool
    value: Any = None


CACHE_POLICIES: dict[str, CachePolicy] = {
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
    """Build a stable, versioned key from normalized tool inputs."""
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


class ToolCache:
    """Redis exact cache that degrades to misses during outages."""

    def __init__(self, redis_client: Any | None) -> None:
        self.redis = redis_client
        self._retry_at = 0.0

    @property
    def enabled(self) -> bool:
        return self.redis is not None

    async def get(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        model_id: str = "",
    ) -> CacheLookup:
        if tool_name not in CACHE_POLICIES or not self.enabled:
            return CacheLookup(hit=False)
        if time.monotonic() < self._retry_at:
            return CacheLookup(hit=False)

        key = tool_cache_key(tool_name, args, model_id=model_id)
        try:
            raw = await self.redis.get(key)
            if raw is None:
                return CacheLookup(hit=False)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            envelope = json.loads(raw)
            return CacheLookup(hit=True, value=envelope["value"])
        except Exception as exc:
            self._mark_unavailable(exc)
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
        if policy is None or not self.enabled or time.monotonic() < self._retry_at:
            return
        if isinstance(value, dict) and value.get("error"):
            return

        key = tool_cache_key(tool_name, args, model_id=model_id)
        envelope = json.dumps(
            {"version": CACHE_VERSION, "value": value},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            await self.redis.set(key, envelope, ex=policy.ttl_seconds)
        except Exception as exc:
            self._mark_unavailable(exc)

    def _mark_unavailable(self, exc: Exception) -> None:
        now = time.monotonic()
        if now >= self._retry_at:
            logger.warning(
                "Redis tool cache unavailable; treating requests as misses for %.0fs: %s",
                REDIS_RETRY_SECONDS,
                exc,
            )
        self._retry_at = now + REDIS_RETRY_SECONDS


async def create_redis_client(url: str, *, enabled: bool) -> Any | None:
    """Create a reconnecting async Redis client without making startup depend on it."""
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
        logger.info("Redis cache and distributed rate limiting enabled")
    except Exception as exc:
        logger.warning("Redis unavailable at startup; fail-open mode enabled: %s", exc)
    return client
