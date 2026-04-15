---
applyTo: "**/*.py"
description: "Use when writing or modifying Python source code. Covers module layout, async patterns, imports, and framework conventions for FastMCP + Microsoft Graph SDK."
---
# Python Source Conventions

## Module Header

Every module starts with `from __future__ import annotations`.

## Commenting Convention

Non-obvious decisions must include an inline comment that answers three questions:

1. **Why it exists** — the intent behind the choice
2. **What breaks without it** — the failure mode if it is removed or changed
3. **Docs link** — a URL the next developer can read for full context

Format (single line for short explanations, multi-line for more complex ones):
```python
# <brief what/why>. Without this, <failure mode>.
# Docs: https://...
```

Apply to:
- Auth guards (`get_access_token() is None` checks)
- Error-code filtering (`if exc.response_status_code != 403:`)
- ETag retry logic
- Magic strings (`orderHint: " !"`)
- Middleware registration calls
- Any SDK pattern that is non-obvious from the code alone (e.g. fresh `HeadersCollection()`, `paginate()` over bare `.get()`)
- Sentinel values (`"*all"`, `select=None`)
- `ctx: Context | None = None` guards

Do **not** add comments to obvious code (simple assignments, straightforward returns).

## Client Logging (`ctx`)

All read tools accept an optional `ctx: Context | None = None` parameter.
FastMCP injects `Context` automatically during dispatch based on the type hint.
The `None` default allows tests to call the function directly without a request context.

Always guard ctx calls so tests pass without mocking the context:
```python
if ctx is not None:
    await ctx.info("Fetching items...")
```

Use `ctx.info()` for user-visible progress on multi-step or paginated operations.
Use `ctx.debug()` for single fast calls where intermediate state is not interesting to the user.
Docs: https://gofastmcp.com/servers/logging


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

## Configuration

- Environment variables via `pydantic_settings.BaseSettings` in `src/config.py`
- Access as `settings.VARIABLE_NAME` from the singleton `settings` instance
