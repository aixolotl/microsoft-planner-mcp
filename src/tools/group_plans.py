from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_plan import PlannerPlan

from ..graph_client_manager import graph_client_manager

group_plans_router = FastMCP("group_plans")


@group_plans_router.tool(name="list-group-plans", annotations={"readOnlyHint": True})
async def list_group_plans(group_id: str) -> list[PlannerPlan]:
    """List all Planner plans belonging to a Microsoft 365 group.

    group_id is the object ID of the group (visible in plan.owner from list-my-plans).
    Requires the Tasks.Read and Group.Read.All delegated permissions.
    Raises ValueError if the authenticated user is not a member of the group (403).
    """
    token = get_access_token()
    if token is None:
        raise ValueError("No access token available")

    graph_client = graph_client_manager.for_user(token.token)
    try:
        result = await graph_client.groups.by_group_id(group_id).planner.plans.get()
    except ODataError as exc:
        if exc.response_status_code == 403:
            code = exc.error.code if exc.error else None
            msg = exc.error.message if exc.error else exc.primary_message
            raise ValueError(f"Access denied for group {group_id!r} ({code}): {msg}") from exc
        raise

    return result.value if result and result.value else []
