"""Unit tests for the response-size middleware registered in src/server.py."""

from __future__ import annotations

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware

from src.server import mcp


def make_server() -> FastMCP:
    # A throwaway server with the middleware instance src.server registered,
    # so the test covers the configured limit without Graph or auth.
    limiter = next(m for m in mcp.middleware if isinstance(m, ResponseLimitingMiddleware))
    server = FastMCP("size-limit-test")
    server.add_middleware(limiter)

    @server.tool
    def list_items(count: int) -> list[dict] | None:
        return [{"id": f"item-{i}", "title": "x" * 1000} for i in range(count)]

    return server


# ---------------------------------------------------------------------------
# Tests: oversized responses
# ---------------------------------------------------------------------------


async def test_oversized_response_is_a_tool_error():
    # Before: the response was truncated to text without structuredContent,
    # which MCP clients reject for a tool with an output schema.
    async with Client(make_server()) as client:
        with pytest.raises(ToolError, match="larger than 500,000 bytes"):
            await client.call_tool("list_items", {"count": 600})


async def test_response_under_the_limit_is_unchanged():
    async with Client(make_server()) as client:
        result = await client.call_tool("list_items", {"count": 3})

    assert result.structured_content == {"result": [{"id": f"item-{i}", "title": "x" * 1000} for i in range(3)]}
