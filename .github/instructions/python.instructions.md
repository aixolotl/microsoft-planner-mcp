---
applyTo: "**/*.py"
description: "Use when writing or modifying Python source code. Covers module layout, async patterns, imports, and framework conventions for FastMCP + Microsoft Graph SDK."
---
# Python Source Conventions

## Module Header

Every module starts with `from __future__ import annotations`.

## Async Patterns

- All Graph SDK calls are async — use `async with graph_client_manager.for_user(token)` for OBO auth
- Never block the event loop; use `await` for all I/O operations

## Tool Routers (`src/tools/`)

- Each domain gets its own `FastMCP` router instance, mounted in `src/server.py`
- Tools must call `get_access_token()` and raise `AuthorizationError` when `None` before any Graph call
- Annotate read-only tools with `annotations={"readOnlyHint": True}`

```python
from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token

from ..graph_client_manager import graph_client_manager

my_router = FastMCP("domain_name")

@my_router.tool(name="tool_name", annotations={"readOnlyHint": True})
async def tool_name() -> SomeType:
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")
    async with graph_client_manager.for_user(token.token) as graph_client:
        ...
```

## Services (`src/services/`)

- Accept a `GraphServiceClient` in `__init__` — no direct dependency on `graph_client_manager`
- Use SDK request builders and `RequestConfiguration` for headers / query parameters
- Use `HeadersCollection()` explicitly — never rely on `RequestConfiguration` default headers (mutable shared state)

## Configuration

- Environment variables via `pydantic_settings.BaseSettings` in `src/config.py`
- Access as `settings.VARIABLE_NAME` from the singleton `settings` instance
