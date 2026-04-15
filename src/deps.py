from __future__ import annotations

from fastmcp import Context
from fastmcp.server.dependencies import get_context


def get_optional_context() -> Context | None:
    """Return the active MCP Context, or None when called outside a request.

    get_context() raises RuntimeError outside a FastMCP request (e.g. in unit
    tests). This wrapper makes it safe to call anywhere without repeating the
    try/except boilerplate at every call site.
    Docs: https://gofastmcp.com/servers/context#via-get_context-function
    """
    try:
        return get_context()
    except RuntimeError:
        return None
