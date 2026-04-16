from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token
from msgraph.generated.models.user import User

from ..deps import get_optional_context
from ..graph_client_manager import graph_client_manager
from ..services.planner_service import PlannerService

me_router = FastMCP("me")

# readOnlyHint=True signals to the MCP client that this tool never mutates
# state, so the client (e.g. Claude, MCP Inspector) can skip confirmation
# prompts. Without it, clients treat the tool as potentially destructive.
# Docs: https://gofastmcp.com/servers/tools#using-annotation-hints
@me_router.tool(
    name="get_me",
    description="Return the authenticated user's profile from Microsoft Graph.",
    tags={"users", "read"},
    annotations={"readOnlyHint": True},
)
async def get_me() -> dict | User | None:
    # get_access_token() returns the FastMCP session token injected by
    # AzureProvider after the OAuth flow completes. It is None when the request
    # arrives without a valid session (e.g. a bare HTTP call without the
    # Authorization header). Raising AuthorizationError here produces a clean
    # MCP error response instead of a confusing AttributeError later.
    # Docs: https://gofastmcp.com/servers/auth/authentication
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    ctx = get_optional_context()

    if ctx is not None:
        await ctx.debug("Fetching authenticated user profile from Microsoft Graph")

    async with graph_client_manager.for_user(token.token) as graph_client:
        svc = PlannerService(graph_client)
        result = await graph_client.me.get()

    return svc.serialize_graph_object(result) if result else None
