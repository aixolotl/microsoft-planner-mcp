from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_plan import PlannerPlan
from msgraph.generated.users.item.planner.plans.plans_request_builder import PlansRequestBuilder

from ..graph_client_manager import graph_client_manager
from ..services.planner_service import PlannerService

plans_router = FastMCP("plans")


# readOnlyHint=True signals to the MCP client that this tool never mutates
# state, allowing it to skip user confirmation prompts for read operations.
# Docs: https://gofastmcp.com/servers/tools#using-annotation-hints
@plans_router.tool(name="list_my_plans", annotations={"readOnlyHint": True})
async def list_my_plans(
    select: str | None = "id,title,owner,details",
) -> list[PlannerPlan] | None:
    """List all Planner plans accessible to the authenticated user.

    Args:
        select: Comma-separated list of PlannerPlan fields to include. Default is "id,title,owner,details". Pass "*all" for all fields.

    Returns:
        A list of PlannerPlan objects, or None if the user has no plans.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    # "*all" is a sentinel that bypasses $select entirely so Graph returns
    # every field. Passing select=None to the SDK achieves this — the SDK
    # omits the $select query parameter from the request URL.
    if select == "*all":
        select = None

    # The Graph SDK's $select parameter requires a list of field names, not a
    # comma-separated string. We split here so callers can use the natural
    # "id,title,owner" syntax without needing to know the SDK's internal shape.
    select_fields = (
        [field.strip() for field in select.split(",") if field.strip()]
        if select is not None
        else None
    )

    async with graph_client_manager.for_user(token.token) as graph_client:
        all_plans = await PlannerService.paginate(
            graph_client.me.planner.plans,
            RequestConfiguration(
                query_parameters=PlansRequestBuilder.PlansRequestBuilderGetQueryParameters(
                    select=select_fields
                )
            ),
        )

    return all_plans or None


@plans_router.tool(name="list_group_plans", annotations={"readOnlyHint": True})
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
            plans = await PlannerService.paginate(graph_client.groups.by_group_id(group_id).planner.plans)
        except ODataError as exc:
            # 403 from the groups endpoint means the user is not a member of
            # that group (or doesn't have permission to read its plans). We
            # convert this to a ValueError with a human-readable message so
            # the LLM receives a clear explanation rather than a raw ODataError
            # stack trace. All other status codes (e.g. 404, 500) are re-raised
            # as-is so genuine failures are not silently swallowed.
            if exc.response_status_code != 403:
                raise
            code = exc.error.code if exc.error else None
            msg = exc.error.message if exc.error else exc.primary_message
            raise ValueError(f"Access denied for group {group_id!r} ({code}): {msg}") from exc

    return plans or None
