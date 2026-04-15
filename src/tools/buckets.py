from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token
from msgraph.generated.models.planner_bucket import PlannerBucket

from ..deps import get_optional_context
from ..graph_client_manager import graph_client_manager
from ..services.planner_service import PlannerService

buckets_router = FastMCP("buckets")


# readOnlyHint=True signals to the MCP client that this tool never mutates
# state, allowing it to skip user confirmation prompts for read operations.
# Docs: https://gofastmcp.com/servers/tools#using-annotation-hints
@buckets_router.tool(name="list_buckets", annotations={"readOnlyHint": True})
async def list_buckets(plan_id: str) -> list[PlannerBucket] | None:
    """List all buckets in a Planner plan.

    Args:
        plan_id: The ID of the plan to list buckets for (from list_my_plans or list_group_plans).

    Returns:
        A list of PlannerBucket objects, or None if the plan has no buckets.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    ctx = get_optional_context()

    if ctx is not None:
        await ctx.info(f"Fetching buckets for plan {plan_id}")

    async with graph_client_manager.for_user(token.token) as graph_client:
        # PlannerService.paginate is used instead of a bare .get() because
        # the Graph API returns paged responses (up to 100 items per page with
        # an @odata.nextLink for subsequent pages). A direct .get() silently
        # drops everything past the first page. paginate() follows all pages
        # via PageIterator and returns a flat list.
        # Docs: https://learn.microsoft.com/en-us/graph/paging
        buckets = await PlannerService.paginate(graph_client.planner.plans.by_planner_plan_id(plan_id).buckets)

    if ctx is not None:
        await ctx.info(f"Found {len(buckets)} bucket(s) in plan {plan_id}")

    return buckets or None
