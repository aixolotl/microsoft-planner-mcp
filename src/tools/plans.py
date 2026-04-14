from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from msgraph.generated.models.planner_plan import PlannerPlan

from ..graph_client_manager import graph_client_manager

plans_router = FastMCP("plans")


@plans_router.tool(name="list-my-plans", annotations={"readOnlyHint": True})
async def list_my_plans() -> list[PlannerPlan]:
    """List all Planner plans accessible to the authenticated user.

    Returns basic plans only — Premium plans are not accessible via this endpoint.
    Requires the Tasks.Read delegated permission.
    """
    token = get_access_token()
    if token is None:
        raise ValueError("No access token available")

    graph_client = graph_client_manager.for_user(token.token)
    result = await graph_client.me.planner.plans.get()

    return result.value if result and result.value else []
