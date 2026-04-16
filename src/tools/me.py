from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token

from ..deps import get_optional_context
from ..graph_client_manager import graph_client_manager
from ..services.base import BasePlannerService

me_router = FastMCP("me")

# readOnlyHint=True signals to the MCP client that this tool never mutates
# state, so the client (e.g. Claude, MCP Inspector) can skip confirmation
# prompts. Without it, clients treat the tool as potentially destructive.
# Docs: https://gofastmcp.com/servers/tools#using-annotation-hints
@me_router.tool(name="get_me", annotations={"readOnlyHint": True})
async def get_me() -> dict | None:
    """Return the authenticated user's profile from Microsoft Graph.

    Returns:
        The authenticated user's identity as a User object.
    """
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
        return BasePlannerService.serialize_graph_object(await graph_client.me.get())
