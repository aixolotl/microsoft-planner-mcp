from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from msgraph.generated.models.planner_bucket import PlannerBucket

from ..graph_client_manager import graph_client_manager

buckets_router = FastMCP("buckets")


@buckets_router.tool(name="list-buckets", annotations={"readOnlyHint": True})
async def list_buckets(plan_id: str) -> list[PlannerBucket]:
    """List all buckets in a Planner plan.

    Requires the Tasks.Read delegated permission.
    """
    token = get_access_token()
    if token is None:
        raise ValueError("No access token available")

    graph_client = graph_client_manager.for_user(token.token)
    result = await graph_client.planner.plans.by_planner_plan_id(plan_id).buckets.get()

    buckets = result.value if result and result.value else []
    return sorted(buckets, key=lambda b: b.order_hint or "")
