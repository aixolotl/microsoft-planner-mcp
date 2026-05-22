from __future__ import annotations
from msgraph.generated.planner.plans.item.planner_plan_item_request_builder import PlannerPlanItemRequestBuilder

from typing import Annotated

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_plan import PlannerPlan
from msgraph.generated.models.planner_plan_details import PlannerPlanDetails
from msgraph.generated.models.planner_user_ids import PlannerUserIds
from msgraph.generated.users.item.planner.plans.plans_request_builder import PlansRequestBuilder

from ..deps import get_optional_context
from ..graph_client_manager import graph_client_manager
from ..services.planner_service import PlannerService

plans_router = FastMCP("plans")


# readOnlyHint=True signals to the MCP client that this tool never mutates
# state, allowing it to skip user confirmation prompts for read operations.
# Docs: https://gofastmcp.com/servers/tools#using-annotation-hints
@plans_router.tool(
    name="list_my_plans",
    description=(
        "List Planner plans explicitly shared with the authenticated user "
        "(via plannerPlanDetails.sharedWith). This does NOT return all plans "
        "in groups the user belongs to — use list_group_plans for that."
    ),
    tags={"plans", "read"},
    annotations={"readOnlyHint": True},
)
async def list_my_plans(
    select: Annotated[str | None, "Comma-separated list of PlannerPlan fields to include. Pass '*all' for all fields."] = "id,title,owner,createdBy,createdDateTime",
) -> list[dict] | None:
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    ctx = get_optional_context()

    if ctx is not None:
        await ctx.info("Fetching Planner plans for the authenticated user")

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
        svc = PlannerService(graph_client)
        try:
            all_plans = await PlannerService.paginate(
                graph_client.me.planner.plans,
                RequestConfiguration(
                    query_parameters=PlansRequestBuilder.PlansRequestBuilderGetQueryParameters(
                        select=select_fields
                    )
                ),
            )
        except ODataError as exc:
            raise RuntimeError(PlannerService.clean_graph_error(exc)) from None

    if ctx is not None:
        await ctx.info(f"Found {len(all_plans)} plan(s)")

    return svc.serialize_graph_list(all_plans) if all_plans else None


@plans_router.tool(
    name="list_group_plans",
    description="List all Planner plans belonging to a Microsoft 365 group.",
    tags={"plans", "read"},
    annotations={"readOnlyHint": True},
)
async def list_group_plans(
    groupId: Annotated[str, "The object ID of the group (from list_my_groups)."],
    select: Annotated[str | None, "Comma-separated list of PlannerPlan fields to include. Pass '*all' for all fields."] = "id,title,owner,createdBy,createdDateTime",
) -> list[dict] | None:
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    ctx = get_optional_context()

    if ctx is not None:
        await ctx.info(f"Fetching Planner plans for group {groupId}")

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
        svc = PlannerService(graph_client)
        try:
            plans = await PlannerService.paginate(
                graph_client.groups.by_group_id(groupId).planner.plans,
                RequestConfiguration(
                    query_parameters=PlansRequestBuilder.PlansRequestBuilderGetQueryParameters(
                        select=select_fields
                    )
                ),
            )
        except ODataError as exc:
            # 403 from the groups endpoint means the user is not a member of
            # that group (or doesn't have permission to read its plans). We
            # convert this to a ValueError with a human-readable message so
            # the LLM receives a clear explanation rather than a raw ODataError
            # stack trace. All other status codes (e.g. 404, 500) are re-raised
            # as-is so genuine failures are not silently swallowed.
            if exc.response_status_code != 403:
                raise RuntimeError(PlannerService.clean_graph_error(exc)) from None
            code = exc.error.code if exc.error else None
            msg = exc.error.message if exc.error else exc.primary_message
            raise ValueError(f"Access denied for group {groupId!r} ({code}): {msg}") from exc

    if ctx is not None:
        await ctx.info(f"Found {len(plans)} plan(s) in group {groupId}")

    return svc.serialize_graph_list(plans) if plans else None


@plans_router.tool(
    name="create_plan",
    description="Create a new Planner plan for a Microsoft 365 group.",
    tags={"plans", "write"},
)
async def create_plan(
    groupId: Annotated[str, "The object ID of the M365 group that will own the plan (from list_my_groups)."],
    title: Annotated[str, "The display title for the new plan."],
) -> dict | None:
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    ctx = get_optional_context()

    if ctx is not None:
        await ctx.info(f"Creating plan '{title}' for group {groupId}")

    body = PlannerPlan()
    body.owner = groupId
    body.title = title

    async with graph_client_manager.for_user(token.token) as graph_client:
        svc = PlannerService(graph_client)
        try:
            result = await graph_client.planner.plans.post(body)
        except ODataError as exc:
            # 403 here typically means the user is not a member of the group or
            # the group does not have a licence for Planner. Convert to a clear
            # ValueError so the LLM receives a readable explanation instead of a
            # raw ODataError stack trace.
            if exc.response_status_code == 403:
                code = exc.error.code if exc.error else None
                msg = exc.error.message if exc.error else exc.primary_message
                raise ValueError(f"Cannot create plan ({code}): {msg}") from exc
            raise RuntimeError(PlannerService.clean_graph_error(exc)) from None

        if result is None:
            return None

        # Auto-share the plan with its creator so it appears in
        # /me/planner/plans (list_my_plans). Without this, the plan is only
        # discoverable via list_group_plans because Graph does not
        # automatically add the creator to plannerPlanDetails.sharedWith.
        # Docs: https://learn.microsoft.com/en-us/graph/api/plannerplandetails-update
        if not result.id:
            # Guard against a missing ID: using "" would produce a request
            # against by_planner_plan_id("") that either silently succeeds or
            # is swallowed by the broad except block, masking the anomaly.
            if ctx is not None:
                await ctx.debug("Skipping auto-share: plan ID unexpectedly missing")
            return svc.serialize_graph_object(result)
        plan_item: PlannerPlanItemRequestBuilder = graph_client.planner.plans.by_planner_plan_id(result.id)
        try:
            me = await graph_client.me.get()
            if me and me.id:
                details: PlannerPlanDetails | None = await plan_item.details.get()
                details_etag = (
                    details.additional_data.get("@odata.etag") if details else None
                )
                if details_etag:
                    shared = PlannerUserIds()
                    shared.additional_data = {me.id: True}
                    patch_body = PlannerPlanDetails()
                    patch_body.shared_with = shared
                    await plan_item.details.patch(
                        patch_body,
                        request_configuration=svc.make_config(details_etag),
                    )
                    if ctx is not None:
                        await ctx.debug(f"Auto-shared plan with creator {me.id}")
        except Exception:
            # Best-effort: if sharing fails the plan was still created
            # successfully. The user can still access it via list_group_plans.
            if ctx is not None:
                await ctx.debug("Could not auto-share plan with creator")

        return svc.serialize_graph_object(result)


@plans_router.tool(
    name="delete_plan",
    description="Delete a Planner plan.",
    tags={"plans", "write"},
    annotations={"destructiveHint": True},
)
async def delete_plan(
    planId: Annotated[str, "The ID of the plan to delete (from list_my_plans or list_group_plans)."],
    etag: Annotated[str, "The current @odata.etag of the plan. Retries once automatically if stale (412/409)."],
) -> str:
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    async with graph_client_manager.for_user(token.token) as graph_client:
        svc = PlannerService(graph_client)
        item = graph_client.planner.plans.by_planner_plan_id(planId)
        await svc.with_retry(
            etag,
            lambda e: item.delete(request_configuration=svc.make_config(e)),
            lambda: svc.refresh_etag(item.get, f"plan {planId!r}"),
        )
    return f"Deleted plan {planId!r}."


# readOnlyHint=True: this tool reads plan details without any side effects.
# Docs: https://gofastmcp.com/servers/tools#using-annotation-hints
@plans_router.tool(
    name="list_plan_categories",
    description=(
        "Get the category label definitions for a Planner plan. "
        "Returns all 25 category slots with their key (e.g. 'category1') and "
        "display name. Slots without a custom label have a null display_name. "
        "Note: category colours are assigned by the Planner UI and are not "
        "stored in the Graph API."
    ),
    tags={"plans", "read"},
    annotations={"readOnlyHint": True, "destructiveHint": False},
)
async def list_plan_categories(
    planId: Annotated[str, "The ID of the plan (from list_my_plans or list_group_plans)."],
) -> list[dict] | None:
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    ctx = get_optional_context()

    if ctx is not None:
        await ctx.debug(f"Fetching category descriptions for plan {planId}")

    async with graph_client_manager.for_user(token.token) as graph_client:
        try:
            details = await graph_client.planner.plans.by_planner_plan_id(planId).details.get()
        except ODataError as exc:
            # 404 means the plan doesn't exist or the user cannot access it.
            # Return a clear message rather than a raw Graph error so the LLM
            # can suggest the caller use list_my_plans or list_group_plans first.
            # Docs: https://learn.microsoft.com/en-us/graph/api/plannerplandetails-get
            if exc.response_status_code == 404:
                raise ValueError(
                    f"Plan {planId!r} not found. Use list_my_plans or list_group_plans to get a valid plan ID."
                ) from exc
            raise RuntimeError(PlannerService.clean_graph_error(exc)) from None

    if details is None or details.category_descriptions is None:
        return None

    # Use the SDK model's deserializer registry to enumerate category keys
    # rather than hardcoding range(1, 26). If Microsoft adds more categories
    # in a future SDK version they will appear automatically.
    # We sort numerically (category1 < category2 < … < category25) because
    # dict key order from get_field_deserializers() is alphabetical and would
    # otherwise produce category1, category10, category11, … category9.
    # Docs: https://learn.microsoft.com/en-us/graph/api/resources/plannercategorydescriptions
    cat_keys = sorted(
        (k for k in details.category_descriptions.get_field_deserializers() if k != "@odata.type"),
        key=lambda k: int(k.removeprefix("category")),
    )
    etag = details.additional_data.get("@odata.etag") if details.additional_data else None
    return [
        {"key": k, "display_name": getattr(details.category_descriptions, k, None), "@odata.etag": etag}
        for k in cat_keys
    ]
