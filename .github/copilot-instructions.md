# Microsoft Planner MCP — Copilot Instructions

## Project Overview

MCP server for Microsoft Planner built with FastMCP and Microsoft Graph SDK. Authenticates via Azure Entra ID On-Behalf-Of flow.

## Stack

- **Python 3.12+** with `from __future__ import annotations` in every module
- **FastMCP** (`fastmcp[apps]`) — MCP server framework with routers, tools, and OAuth
- **Microsoft Graph SDK** (`msgraph-sdk`) — Planner API via `GraphServiceClient`
- **Pydantic Settings** — environment-based configuration
- **pytest + pytest-asyncio** (auto mode) — testing
- **uv** — package manager and task runner

## Project Structure

- `src/` — application package
  - `src/server.py` — FastMCP app, middleware, route mounting
  - `src/config.py` — `Settings` via `pydantic_settings.BaseSettings`
  - `src/auth_provider.py` — Azure OAuth provider
  - `src/graph_client_manager.py` — singleton `GraphClientManager` with per-user OBO clients
  - `src/query_parameters.py` — custom kiota query parameter dataclasses (avoids deprecated SDK classes)
  - `src/services/` — business logic wrapping Graph SDK calls
  - `src/tools/` — FastMCP tool routers (one router per domain, mounted in `server.py`)
- `tests/` — unit tests mirroring `src/` structure

## Conventions

- Tool routers live in `src/tools/` as separate `FastMCP` instances mounted on the main app
- Services in `src/services/` accept a `GraphServiceClient` and encapsulate Graph API logic
- All Graph calls use `async with graph_client_manager.for_user(token)` for OBO auth
- Tools that require business logic instantiate the relevant service inside the `async with` block: `service = PlannerService(graph_client)`
- Simple direct Graph reads (no pagination, no retry, no ETag) may call the SDK directly from the tool without a service
- Tools must check `get_access_token()` and raise `AuthorizationError` when `None`
- Custom query parameter dataclasses belong in `src/query_parameters.py` — do not import deprecated SDK `*RequestBuilderGetQueryParameters` or `*RequestBuilderGetRequestConfiguration` classes, which emit `DeprecationWarning` at import time
- Run tests with `uv run pytest -v`
- Run server with `uv run uvicorn src.server:app --host 0.0.0.0 --port 8000 --reload`
