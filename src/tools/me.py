from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token
from msgraph.generated.models.user import User

from ..graph_client_manager import graph_client_manager

me_router = FastMCP("me")

@me_router.tool(annotations={"readOnlyHint": True})
async def get_me() -> User | None:
    """Return the authenticated user's identity, verified via Microsoft Graph."""
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    async with graph_client_manager.for_user(token.token) as graph_client:
        return await graph_client.me.get()
