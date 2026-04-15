from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.models.planner_plan import PlannerPlan
from msgraph.generated.users.item.planner.plans.plans_request_builder import PlansRequestBuilder

from ..graph_client_manager import graph_client_manager

plans_router = FastMCP("plans")

# List all Planner plans accessible to the authenticated user.
#  Returns basic plans only — Premium plans are not accessible via this endpoint.
#  Requires the Tasks.Read delegated permission.
@plans_router.tool(name="list_my_plans", annotations={"readOnlyHint": True})
async def list_my_plans(
    select: str | None = "id,title,owner,details",
) -> list[PlannerPlan]:
    """List all Planner plans accessible to the authenticated user.

    Args:
        select: Comma-separated list of PlannerPlan properties to include in the response. Default is "id,title,owner,details", pass "*all" for all fields.

    Returns:
        A list of PlannerPlan objects.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    if select == "*all":
        select = None  # No need to specify $select if we want all fields

    normalized_select = (
        [field.strip() for field in select.split(",") if field.strip()]
        if select is not None
        else None
    )

    query_parameters = PlansRequestBuilder.PlansRequestBuilderGetQueryParameters(
        select=normalized_select,
    )
    request_configuration = RequestConfiguration(query_parameters=query_parameters)

    async with graph_client_manager.for_user(token.token) as graph_client:
        result = await graph_client.me.planner.plans.get(request_configuration=request_configuration)

    return result.value if result and result.value else []
