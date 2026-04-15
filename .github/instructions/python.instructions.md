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
- Tools that require business logic (pagination, retry, ETag handling) instantiate the relevant service **inside** the `async with` block rather than calling the SDK directly
- Simple direct Graph reads (no pagination, no retry, no ETag) may call the SDK directly

```python
from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token

from ..graph_client_manager import graph_client_manager
from ..services.planner_service import PlannerService

my_router = FastMCP("domain_name")

# Simple direct read — no service needed
@my_router.tool(name="get_thing", annotations={"readOnlyHint": True})
async def get_thing(thing_id: str) -> Thing | None:
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")
    async with graph_client_manager.for_user(token.token) as graph_client:
        return await graph_client.planner.things.by_id(thing_id).get()

# Paginated / business logic — delegate to service
@my_router.tool(name="list_things", annotations={"readOnlyHint": True})
async def list_things() -> list[Thing] | None:
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")
    async with graph_client_manager.for_user(token.token) as graph_client:
        service = PlannerService(graph_client)
        results = await service.list_things()
    return results or None
```

## Services (`src/services/`)

- Accept a `GraphServiceClient` in `__init__` — no direct dependency on `graph_client_manager`
- Use `RequestConfiguration` for headers / query parameters
- Use `HeadersCollection()` explicitly — never rely on `RequestConfiguration` default headers (mutable shared state)
- Paginated reads follow next-link until exhaustion; accumulate results into a list

## Query Parameters (`src/query_parameters.py`)

The Graph SDK's generated `*RequestBuilderGetQueryParameters` and `*RequestBuilderGetRequestConfiguration` classes emit `DeprecationWarning` at **import time** (the `warn()` call is in the class body). Do not import them.

Instead, define a plain `@dataclass` in `src/query_parameters.py` that implements `get_query_parameter(self, original_name: str) -> str`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

@dataclass
class MyGetQueryParameters:
    select: Optional[list[str]] = None

    def get_query_parameter(self, original_name: str) -> str:
        if original_name == "select":
            return "%24select"
        return original_name
```

Pass it via `RequestConfiguration(query_parameters=MyGetQueryParameters(...))` and suppress the resulting type checker mismatch with `# type: ignore[arg-type]` on the `.get()` call site.

## Configuration

- Environment variables via `pydantic_settings.BaseSettings` in `src/config.py`
- Access as `settings.VARIABLE_NAME` from the singleton `settings` instance
