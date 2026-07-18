"""Redis 分布式令牌桶，并提供进程内降级实现。

Lua 脚本把“补充令牌、判断、扣减和设置 TTL”放在 Redis 端原子执行，多进程部署
也共享同一用户额度；Redis 不可用时切换到本进程内存桶，保证主服务仍可使用。
"""

import logging
import math
import time
from typing import Any

from fastapi import Depends, HTTPException, Request

from app.database.models import User
from app.middleware.auth import get_current_user

RATE_LIMIT = 20
REFILL_RATE = 1.0 / 3.0  # tokens per second
MAX_TOKENS = 20
BUCKET_TTL_MS = 120_000
REDIS_RETRY_SECONDS = 30.0
logger = logging.getLogger("chatbot.rate_limit")

TOKEN_BUCKET_SCRIPT = """
local now_parts = redis.call('TIME')
local now = tonumber(now_parts[1]) + tonumber(now_parts[2]) / 1000000
local values = redis.call('HMGET', KEYS[1], 'tokens', 'updated_at')
local tokens = tonumber(values[1]) or tonumber(ARGV[1])
local updated_at = tonumber(values[2]) or now
local refill_rate = tonumber(ARGV[2])
local max_tokens = tonumber(ARGV[1])

tokens = math.min(max_tokens, tokens + math.max(0, now - updated_at) * refill_rate)
local allowed = 0
local retry_after = 0
if tokens >= 1 then
    tokens = tokens - 1
    allowed = 1
else
    retry_after = math.ceil((1 - tokens) / refill_rate)
end

redis.call('HSET', KEYS[1], 'tokens', tokens, 'updated_at', now)
redis.call('PEXPIRE', KEYS[1], ARGV[3])
return {allowed, retry_after}
"""


class InMemoryRateLimiter:
    """单进程降级桶；只在 Redis 关闭或短暂不可用时使用。"""
    def __init__(self) -> None:
        self._buckets: dict[str, tuple[float, float]] = {}

    async def consume(self, user_id: str) -> tuple[bool, int]:
        now = time.monotonic()
        last_refill, tokens = self._buckets.get(
            user_id,
            (now, float(MAX_TOKENS)),
        )
        tokens = min(MAX_TOKENS, tokens + (now - last_refill) * REFILL_RATE)
        if tokens >= 1.0:
            self._buckets[user_id] = (now, tokens - 1.0)
            return True, 0
        self._buckets[user_id] = (now, tokens)
        return False, max(1, math.ceil((1.0 - tokens) / REFILL_RATE))


class RedisRateLimiter:
    """原子分布式限流器；Redis 异常后熔断 30 秒并使用本地桶。"""

    def __init__(self, redis_client: Any | None) -> None:
        self.redis = redis_client
        self.fallback = InMemoryRateLimiter()
        self._retry_at = 0.0

    async def consume(self, user_id: str) -> tuple[bool, int]:
        if self.redis is None or time.monotonic() < self._retry_at:
            return await self.fallback.consume(user_id)
        try:
            result = await self.redis.eval(
                TOKEN_BUCKET_SCRIPT,
                1,
                f"chatbot:rate_limit:{user_id}",
                MAX_TOKENS,
                REFILL_RATE,
                BUCKET_TTL_MS,
            )
            return bool(int(result[0])), int(result[1])
        except Exception as exc:
            now = time.monotonic()
            if now >= self._retry_at:
                logger.warning(
                    "Redis rate limiter unavailable; using process-local fallback for %.0fs: %s",
                    REDIS_RETRY_SECONDS,
                    exc,
                )
            self._retry_at = now + REDIS_RETRY_SECONDS
            return await self.fallback.consume(user_id)


async def check_rate_limit(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> None:
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        limiter = RedisRateLimiter(None)
        request.app.state.rate_limiter = limiter
    allowed, retry_after = await limiter.consume(str(current_user.id))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"请求过于频繁，请 {retry_after} 秒后再试",
            headers={"Retry-After": str(retry_after)},
        )
