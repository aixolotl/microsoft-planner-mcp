from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token
from msgraph.generated.models.group import Group

from ..deps import get_optional_context
from ..graph_client_manager import graph_client_manager
from ..services.planner_service import PlannerService

groups_router = FastMCP("groups")


@groups_router.tool(name="list_my_groups", annotations={"readOnlyHint": True})
async def list_my_groups() -> list[Group] | None:
    """List all Microsoft 365 groups the authenticated user is a member of.

    Returns:
        A list of Group objects, or None if the user belongs to no groups.
        Each group's id field can be used as the group_id for list_group_plans and create_plan.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    ctx = get_optional_context()

    if ctx is not None:
        await ctx.debug("Fetching Microsoft 365 group memberships for the authenticated user")

    async with graph_client_manager.for_user(token.token) as graph_client:
        # /me/memberOf returns all directory objects the user belongs to, not
        # just groups. We use /me/transitiveMemberOf/microsoft.graph.group to
        # filter to M365 groups only via OData cast. Without the cast, the
        # response includes roles, admin units, and other non-group objects that
        # cannot be passed to list_group_plans or create_plan.
        # Docs: https://learn.microsoft.com/en-us/graph/api/user-list-transitivememberof
        groups = await PlannerService.paginate(
            graph_client.me.transitive_member_of.graph_group
        )

    if ctx is not None:
        await ctx.debug(f"Found {len(groups)} group(s)")

    return groups or None
