from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_bucket import PlannerBucket

from ..deps import get_optional_context
from ..graph_client_manager import graph_client_manager
from ..services.planner_service import PlannerService

buckets_router = FastMCP("buckets")


# readOnlyHint=True signals to the MCP client that this tool never mutates
# state, allowing it to skip user confirmation prompts for read operations.
# Docs: https://gofastmcp.com/servers/tools#using-annotation-hints
@buckets_router.tool(
    name="list_buckets",
    description="List all buckets in a Planner plan.",
    tags={"buckets", "read"},
    annotations={"readOnlyHint": True},
)
async def list_buckets(
    planId: Annotated[str, "The ID of the plan to list buckets for (from list_my_plans or list_group_plans)."],
) -> list[dict] | None:
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    ctx = get_optional_context()

    if ctx is not None:
        await ctx.info(f"Fetching buckets for plan {planId}")

    async with graph_client_manager.for_user(token.token) as graph_client:
        svc = PlannerService(graph_client)
        # PlannerService.paginate is used instead of a bare .get() because
        # the Graph API returns paged responses (up to 100 items per page with
        # an @odata.nextLink for subsequent pages). A direct .get() silently
        # drops everything past the first page. paginate() follows all pages
        # via PageIterator and returns a flat list.
        # Docs: https://learn.microsoft.com/en-us/graph/paging
        try:
            buckets = await PlannerService.paginate(graph_client.planner.plans.by_planner_plan_id(planId).buckets)
        except ODataError as exc:
            raise RuntimeError(PlannerService.clean_graph_error(exc)) from None

    if ctx is not None:
        await ctx.info(f"Found {len(buckets)} bucket(s) in plan {planId}")

    return svc.serialize_graph_list(buckets) if buckets else None


@buckets_router.tool(
    name="create_bucket",
    description="Create a new bucket in a Planner plan.",
    tags={"buckets", "write"},
)
async def create_bucket(
    planId: Annotated[str, "The ID of the plan to create the bucket in (from list_my_plans or list_group_plans)."],
    name: Annotated[str, "The display name for the new bucket."],
) -> dict | None:
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    ctx = get_optional_context()

    if ctx is not None:
        await ctx.info(f"Creating bucket '{name}' in plan {planId}")

    body = PlannerBucket()
    body.plan_id = planId
    body.name = name
    # orderHint " !" is the Graph API sentinel meaning "place first in order".
    # Without an orderHint, Graph rejects the POST with 400 Bad Request.
    # Docs: https://learn.microsoft.com/en-us/graph/api/resources/planner-order-hint-format
    body.order_hint = " !"

    async with graph_client_manager.for_user(token.token) as graph_client:
        svc = PlannerService(graph_client)
        try:
            result = await graph_client.planner.buckets.post(body)
            return svc.serialize_graph_object(result) if result else None
        except ODataError as exc:
            # 403 means the user lacks write access to the plan. Convert to a
            # clear ValueError so the LLM receives a readable explanation.
            if exc.response_status_code == 403:
                code = exc.error.code if exc.error else None
                msg = exc.error.message if exc.error else exc.primary_message
                raise ValueError(f"Cannot create bucket ({code}): {msg}") from exc
            raise RuntimeError(PlannerService.clean_graph_error(exc)) from None


@buckets_router.tool(
    name="delete_bucket",
    description="Delete a Planner bucket.",
    tags={"buckets", "write"},
    annotations={"destructiveHint": True},
)
async def delete_bucket(
    bucketId: Annotated[str, "The ID of the bucket to delete (from list_buckets)."],
    etag: Annotated[str, "The current @odata.etag of the bucket. Retries once automatically if stale (412/409)."],
) -> str:
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    async with graph_client_manager.for_user(token.token) as graph_client:
        svc = PlannerService(graph_client)
        item = graph_client.planner.buckets.by_planner_bucket_id(bucketId)
        await svc.with_retry(
            etag,
            lambda e: item.delete(request_configuration=svc.make_config(e)),
            lambda: svc.refresh_etag(item.get, f"bucket {bucketId!r}"),
        )
    return f"Deleted bucket {bucketId!r}."
