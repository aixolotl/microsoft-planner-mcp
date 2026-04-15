from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_plan import PlannerPlan

from ..graph_client_manager import graph_client_manager

group_plans_router = FastMCP("group_plans")


@group_plans_router.tool(name="list_group_plans", annotations={"readOnlyHint": True})
async def list_group_plans(group_id: str) -> list[PlannerPlan] | None:
    """List all Planner plans belonging to a Microsoft 365 group.

    Args:
        group_id: The object ID of the group (available as plan.owner from list_my_plans).

    Returns:
        A list of PlannerPlan objects belonging to the group, or None if the group has no plans.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    async with graph_client_manager.for_user(token.token) as graph_client:
        try:
            result = await graph_client.groups.by_group_id(group_id).planner.plans.get()
        except ODataError as exc:
            if exc.response_status_code != 403:
                raise
            code = exc.error.code if exc.error else None
            msg = exc.error.message if exc.error else exc.primary_message
            raise ValueError(f"Access denied for group {group_id!r} ({code}): {msg}") from exc

    return result.value if result else None
