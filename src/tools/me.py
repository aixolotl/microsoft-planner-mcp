from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token, get_context
from msgraph.generated.models.user import User

from ..graph_client_manager import graph_client_manager

me_router = FastMCP("me")

# readOnlyHint=True signals to the MCP client that this tool never mutates
# state, so the client (e.g. Claude, MCP Inspector) can skip confirmation
# prompts. Without it, clients treat the tool as potentially destructive.
# Docs: https://gofastmcp.com/servers/tools#using-annotation-hints
@me_router.tool(name="get_me", annotations={"readOnlyHint": True})
async def get_me() -> User | None:
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

    # get_context() retrieves the active MCP context inside a FastMCP request.
    # It raises RuntimeError when called outside a request (e.g. unit tests);
    # ctx = None in that case so logging is safely skipped.
    # Docs: https://gofastmcp.com/servers/context#via-get_context-function
    try:
        ctx = get_context()
    except RuntimeError:
        ctx = None

    if ctx is not None:
        await ctx.debug("Fetching authenticated user profile from Microsoft Graph")

    async with graph_client_manager.for_user(token.token) as graph_client:
        return await graph_client.me.get()
