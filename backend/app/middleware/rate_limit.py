"""
Simple in-memory token bucket rate limiter.
Per-user rate limiting for API endpoints.
"""

import time
from collections import defaultdict

from fastapi import Depends, HTTPException

from app.database.models import User
from app.middleware.auth import get_current_user

# Rate limit configuration
RATE_LIMIT = 20  # requests per minute
REFILL_RATE = 3.0  # seconds per token refill
MAX_TOKENS = 20  # burst capacity

_buckets: dict[str, tuple[float, float]] = {}  # user_id -> (last_refill, tokens)


def _get_bucket(user_id: str) -> tuple[float, float]:
    """Get or create a token bucket for a user. Returns (last_refill, tokens)."""
    now = time.monotonic()
    bucket = _buckets.get(user_id, (now, float(MAX_TOKENS)))

    last_refill, tokens = bucket
    # Refill tokens based on elapsed time
    elapsed = now - last_refill
    new_tokens = min(MAX_TOKENS, tokens + elapsed / REFILL_RATE)
    _buckets[user_id] = (now, new_tokens)
    return (now, new_tokens)


def _consume_token(user_id: str) -> bool:
    """Try to consume a token. Returns True if allowed, False if rate limited."""
    _last_refill, tokens = _get_bucket(user_id)
    if tokens >= 1.0:
        _buckets[user_id] = (time.monotonic(), tokens - 1.0)
        return True
    return False


async def check_rate_limit(
    current_user: User = Depends(get_current_user),
) -> None:
    """FastAPI dependency: check if the current user is rate limited."""
    user_id = current_user.id
    if not _consume_token(user_id):
        # Calculate retry-after
        _last_refill, tokens = _get_bucket(user_id)
        wait = max(1, int((1.0 - tokens) * REFILL_RATE))
        raise HTTPException(
            status_code=429,
            detail=f"请求过于频繁，请 {wait} 秒后再试",
            headers={"Retry-After": str(wait)},
        )
