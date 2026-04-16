from __future__ import annotations

from typing import Annotated

from pydantic import Field

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_plan import PlannerPlan
from msgraph.generated.users.item.planner.plans.plans_request_builder import PlansRequestBuilder

from ..deps import get_optional_context
from ..graph_client_manager import graph_client_manager
from ..services.plan_service import PlanService

plans_router = FastMCP("plans")


# readOnlyHint=True signals to the MCP client that this tool never mutates
# state, allowing it to skip user confirmation prompts for read operations.
# Docs: https://gofastmcp.com/servers/tools#using-annotation-hints
@plans_router.tool(name="list_my_plans", annotations={"readOnlyHint": True})
async def list_my_plans(
    select: Annotated[
        str | None,
        Field(
            description=(
                "Comma-separated PlannerPlan fields to return. "
                "Default: 'id,title,owner,createdBy,createdDateTime'. "
                "Pass '*all' for all fields."
            ),
            # FastMCP/Pydantic enforces min_length=1, rejecting "" before the
            # function body runs.
            # Docs: https://gofastmcp.com/servers/tools#advanced-metadata-with-field
            min_length=1,
        ),
    ] = "id,title,owner,createdBy,createdDateTime",
) -> list[dict] | None:
    """List Planner plans directly associated with the authenticated user.

    Note: this endpoint only returns plans the user personally owns or has a direct
    relationship with. Group-owned plans (the common case) do not appear here —
    use list_group_plans with a group_id from list_my_groups instead.

    Args:
        select: Comma-separated list of PlannerPlan fields to include. Default is "id,title,owner,createdBy,createdDateTime". Pass "*all" for all fields.

    Returns:
        A list of PlannerPlan objects, or None if the user has no directly-owned plans.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    ctx = get_optional_context()

    if ctx is not None:
        await ctx.info("Fetching Planner plans for the authenticated user")

    # "" is rejected at the MCP protocol level by Field(min_length=1) above.
    # "*all" sentinel bypasses $select so Graph returns every field.
    # Docs: https://learn.microsoft.com/en-us/graph/query-parameters#select-parameter
    if select == "*all":
        select = None

    # Kiota QueryParameters dataclasses require list[str] for $select, not a
    # comma-separated string. Without the split, "id,title" would be sent as a
    # single field name "id,title" rather than two separate fields.
    # Docs: https://github.com/microsoft/kiota-abstractions-python
    select_fields = (
        [field.strip() for field in select.split(",") if field.strip()]
        if select is not None
        else None
    )
    # Guard: comma-only strings (", ,") survive min_length=1 but produce zero
    # tokens after split — silently omitting $select. Reject explicitly.
    # Docs: https://gofastmcp.com/servers/tools#advanced-metadata-with-field
    if select_fields is not None and not select_fields:
        raise ValueError(
            f"select resolved to no fields after parsing (input: {select!r}). "
            "Pass None or '*all' to return all fields."
        )

    async with graph_client_manager.for_user(token.token) as graph_client:
        try:
            all_plans = await PlanService(graph_client, serialize=True).paginate(
                graph_client.me.planner.plans,
                RequestConfiguration(
                    query_parameters=PlansRequestBuilder.PlansRequestBuilderGetQueryParameters(
                        select=select_fields
                    )
                ),
            )
        except ODataError as exc:
            # 400 from $select with an unrecognised field name; re-raise as
            # ValueError so the LLM receives the Graph error message directly.
            # Without this, callers see a raw APIError stack trace.
            # Docs: https://learn.microsoft.com/en-us/graph/errors#http-status-codes
            if exc.response_status_code != 400:
                raise
            msg = exc.error.message if exc.error else exc.primary_message
            raise ValueError(f"Invalid select: {msg}") from exc

    if ctx is not None:
        await ctx.info(f"Found {len(all_plans)} plan(s)")

    return all_plans or None


@plans_router.tool(name="list_group_plans", annotations={"readOnlyHint": True})
async def list_group_plans(
    group_id: Annotated[
        str,
        Field(description="The object ID of the group (from list_my_groups)."),
    ],
    select: Annotated[
        str | None,
        Field(
            description=(
                "Comma-separated PlannerPlan fields to return. "
                "Default: 'id,title,owner,createdBy,createdDateTime'. "
                "Pass '*all' for all fields."
            ),
            min_length=1,
        ),
    ] = "id,title,owner,createdBy,createdDateTime",
) -> list[dict] | None:
    """List all Planner plans belonging to a Microsoft 365 group.

    Args:
        group_id: The object ID of the group (from list_my_groups).
        select: Comma-separated list of PlannerPlan fields to include. Default is "id,title,owner,createdBy,createdDateTime". Pass "*all" for all fields.

    Returns:
        A list of PlannerPlan objects belonging to the group, or None if the group has no plans.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    ctx = get_optional_context()

    if ctx is not None:
        await ctx.info(f"Fetching Planner plans for group {group_id}")

    # "" is rejected at the MCP protocol level by Field(min_length=1) above.
    # "*all" sentinel — see list_my_plans for rationale.
    # Docs: https://learn.microsoft.com/en-us/graph/query-parameters#select-parameter
    if select == "*all":
        select = None

    # Kiota QueryParameters dataclasses require list[str] for $select.
    # Docs: https://github.com/microsoft/kiota-abstractions-python
    select_fields = (
        [field.strip() for field in select.split(",") if field.strip()]
        if select is not None
        else None
    )
    # Guard: comma-only strings (", ,") survive min_length=1 but produce zero
    # tokens after split — silently omitting $select. Reject explicitly.
    # Docs: https://gofastmcp.com/servers/tools#advanced-metadata-with-field
    if select_fields is not None and not select_fields:
        raise ValueError(
            f"select resolved to no fields after parsing (input: {select!r}). "
            "Pass None or '*all' to return all fields."
        )

    async with graph_client_manager.for_user(token.token) as graph_client:
        try:
            plans = await PlanService(graph_client, serialize=True).paginate(
                graph_client.groups.by_group_id(group_id).planner.plans,
                RequestConfiguration(
                    query_parameters=PlansRequestBuilder.PlansRequestBuilderGetQueryParameters(
                        select=select_fields
                    )
                ),
            )
        except ODataError as exc:
            # 400: $select contained an unrecognised field name.
            # 404: group_id does not exist.
            # 403: user is not a member or lacks permission.
            # All other codes are re-raised so genuine failures surface.
            # Docs: https://learn.microsoft.com/en-us/graph/api/resources/planner-overview#common-planner-error-conditions
            # Docs: https://learn.microsoft.com/en-us/graph/errors#http-status-codes
            if exc.response_status_code == 400:
                msg = exc.error.message if exc.error else exc.primary_message
                raise ValueError(f"Invalid select: {msg}") from exc
            if exc.response_status_code == 404:
                raise ValueError(f"Group '{group_id}' not found.") from exc
            if exc.response_status_code != 403:
                raise
            code = exc.error.code if exc.error else None
            msg = exc.error.message if exc.error else exc.primary_message
            raise ValueError(
                f"Access denied for group '{group_id}' ({code}): {msg}. "
                "Verify the user is a member of the group."
            ) from exc

    if ctx is not None:
        await ctx.info(f"Found {len(plans)} plan(s) in group {group_id}")

    return plans or None


@plans_router.tool(name="create_plan")
async def create_plan(group_id: str, title: str) -> dict | None:
    """Create a new Planner plan for a Microsoft 365 group.

    Args:
        group_id: The object ID of the M365 group that will own the plan (from list_my_groups).
        title: The display title for the new plan.

    Returns:
        The created PlannerPlan object.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    ctx = get_optional_context()

    if ctx is not None:
        await ctx.info(f"Creating plan '{title}' for group {group_id}")

    body = PlannerPlan()
    body.owner = group_id
    body.title = title

    async with graph_client_manager.for_user(token.token) as graph_client:
        try:
            plan = await graph_client.planner.plans.post(body)
        except ODataError as exc:
            # 403 here typically means the user is not a member of the group or
            # the group does not have a licence for Planner. Convert to a clear
            # ValueError so the LLM receives a readable explanation instead of a
            # raw ODataError stack trace.
            if exc.response_status_code != 403:
                raise
            code = exc.error.code if exc.error else None
            msg = exc.error.message if exc.error else exc.primary_message
            raise ValueError(f"Cannot create plan ({code}): {msg}") from exc
    return PlanService.serialize_graph_object(plan)


@plans_router.tool(name="delete_plan")
async def delete_plan(plan_id: str, etag: str) -> dict:
    """Delete a Planner plan.

    Args:
        plan_id: The ID of the plan to delete (from list_my_plans or list_group_plans).
        etag: The current @odata.etag of the plan. PlanService retries once with a refreshed ETag on 412/409.

    Returns:
        A dict confirming deletion: {"deleted": true, "id": "<plan_id>"}.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    async with graph_client_manager.for_user(token.token) as graph_client:
        svc = PlanService(graph_client)
        await svc.delete_plan(plan_id, etag)
    return {"deleted": True, "id": plan_id}
