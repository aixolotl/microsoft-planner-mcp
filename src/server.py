from __future__ import annotations

import logging

from fastmcp import FastMCP
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from fastmcp.server.middleware.logging import StructuredLoggingMiddleware
from fastmcp.server.middleware.rate_limiting import SlidingWindowRateLimitingMiddleware
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware
from fastmcp.server.middleware.timing import TimingMiddleware
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from .auth_provider import auth
from .config import settings
from .tools.buckets import buckets_router
from .tools.me import me_router
from .tools.plans import plans_router
from .tools.tasks import tasks_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

mcp = FastMCP(
    "Microsoft Planner MCP",
    auth=auth,
    instructions=(
        "MCP server for Microsoft Planner. "
        "Authenticate with your Microsoft account to read and manage Planner tasks."
    ),
    mask_error_details=True,
    version="1.0.0",
)

# --- Middleware stack (order matters: first added = outermost = runs first on
#     the way in and last on the way out) ---

# Catches any unhandled exception from all downstream middleware and tools,
# logs the full stack trace server-side, and converts it to a clean MCP error
# response. Without this, an unexpected exception in a tool (e.g. a Graph SDK
# bug or a network blip) would propagate as an unformatted 500 and may crash
# the request context entirely.
mcp.add_middleware(ErrorHandlingMiddleware(include_traceback=True))

# Enforces a 60 req/min ceiling per client using a sliding window. LLM agents
# can fan out tool calls rapidly (e.g. fetching details for every task in a
# plan). Without this, an unconstrained agent could exhaust the Microsoft Graph
# throttling quota (10,000 req/10min per tenant), causing 429s for all users.
mcp.add_middleware(SlidingWindowRateLimitingMiddleware(max_requests=60, window_minutes=1))

# Records wall-clock duration for every MCP operation and emits it to the log.
# Useful for spotting which Graph API calls are slow (e.g. list_tasks
# paginating a large plan). Without this there is no observability into latency
# and slow requests are invisible until users complain.
mcp.add_middleware(TimingMiddleware())

# Emits one structured JSON log line per request/response — method, status,
# timing, and client info. Placed after TimingMiddleware so the log record
# includes the duration. Without this, the only logs are the basicConfig lines
# from uvicorn and the Python root logger, which are hard to query in Docker
# log aggregators (Datadog, CloudWatch, etc.).
mcp.add_middleware(StructuredLoggingMiddleware())

# Truncates tool responses that exceed 500 KB. Microsoft Graph list endpoints
# (list_tasks, list_my_tasks) can return very large payloads when a plan has
# many tasks with full field sets. Without this, an oversized response can
# overflow the LLM's context window, causing the client to error or silently
# drop content.
mcp.add_middleware(ResponseLimitingMiddleware(max_size=500_000))

mcp.mount(me_router)
mcp.mount(plans_router)
mcp.mount(tasks_router)
mcp.mount(buckets_router)


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    return JSONResponse({"status": "healthy", "service": "planner-mcp"})


@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
async def oauth_protected_resource_root(request: Request) -> RedirectResponse:
    # Some OAuth clients (e.g. MCP Inspector) request the root path; redirect
    # to the FastMCP-managed path that carries the actual metadata.
    return RedirectResponse(
        url="/.well-known/oauth-protected-resource/mcp", status_code=301
    )


# ASGI app — used by uvicorn in production: uvicorn src.server:app
app = mcp.http_app(
    stateless_http=True,
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=settings.ALLOWED_ORIGINS,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["mcp-protocol-version", "mcp-session-id", "Authorization", "Content-Type"],
            expose_headers=["mcp-session-id"],
        )
    ],
)

if __name__ == "__main__":
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=8000,
        stateless_http=True,
    )
