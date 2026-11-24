"""Unit tests for the rate-limiting middleware registered in src/server.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.server.middleware.rate_limiting import (
    RateLimitError,
    SlidingWindowRateLimitingMiddleware,
)

from src.config import settings
from src.server import mcp

# Patch at the usage module: src.server binds get_access_token at import, so
# patching fastmcp.server.dependencies afterwards would not reach it.
MODULE = "src.server"


def make_token(oid: str) -> MagicMock:
    token = MagicMock()
    token.claims = {"oid": oid, "sub": f"sub-{oid}"}
    return token


def get_rate_limiter() -> SlidingWindowRateLimitingMiddleware:
    limiter = next(m for m in mcp.middleware if isinstance(m, SlidingWindowRateLimitingMiddleware))
    # The middleware is a module-level singleton; start each test with empty
    # windows so one test's requests do not count against the next.
    limiter.limiters.clear()
    return limiter


async def send_as(limiter: SlidingWindowRateLimitingMiddleware, oid: str) -> None:
    # create=True: the request is sent the same way whether or not the module
    # reads the token, so the test fails on the assertion, not on the patch.
    with patch(f"{MODULE}.get_access_token", return_value=make_token(oid), create=True):
        await limiter.on_request(MagicMock(), AsyncMock(return_value="ok"))


# ---------------------------------------------------------------------------
# Tests: rate limit is applied per signed-in user
# ---------------------------------------------------------------------------


async def test_one_user_at_the_limit_does_not_block_another_user():
    limiter = get_rate_limiter()
    for _ in range(settings.RATE_LIMIT_MAX_REQUESTS):
        await send_as(limiter, "oid-busy-user")

    # The other user has sent nothing yet. Before the fix every request shared
    # one "global" bucket, so this call raised RateLimitError.
    await send_as(limiter, "oid-quiet-user")


async def test_user_over_the_limit_is_still_rejected():
    limiter = get_rate_limiter()
    for _ in range(settings.RATE_LIMIT_MAX_REQUESTS):
        await send_as(limiter, "oid-over-limit")

    with pytest.raises(RateLimitError):
        await send_as(limiter, "oid-over-limit")
