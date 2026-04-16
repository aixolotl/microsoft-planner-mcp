from __future__ import annotations

from typing import Annotated

from pydantic import Field

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_bucket import PlannerBucket
from msgraph.generated.planner.buckets.item.tasks.tasks_request_builder import TasksRequestBuilder as BucketTasksRequestBuilder

from ..deps import get_optional_context
from ..graph_client_manager import graph_client_manager
from ..services.bucket_service import BucketService

buckets_router = FastMCP("buckets")


# readOnlyHint=True signals to the MCP client that this tool never mutates
# state, allowing it to skip user confirmation prompts for read operations.
# Docs: https://gofastmcp.com/servers/tools#using-annotation-hints
@buckets_router.tool(name="list_buckets", annotations={"readOnlyHint": True})
async def list_buckets(
    plan_id: Annotated[
        str,
        Field(description="The plan ID to list buckets for (from list_my_plans or list_group_plans)."),
    ],
) -> list[dict] | None:
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
        # paginate() is used instead of a bare .get() because the Graph API
        # returns paged responses (up to 100 items per page with an
        # @odata.nextLink for subsequent pages). A direct .get() silently
        # drops everything past the first page. paginate() follows all pages
        # via PageIterator and returns a flat list.
        # Docs: https://learn.microsoft.com/en-us/graph/paging
        try:
            buckets = await BucketService(graph_client, serialize=True).paginate(graph_client.planner.plans.by_planner_plan_id(plan_id).buckets)
        except ODataError as exc:
            # 404 means plan_id does not exist. Convert to a clear ValueError
            # rather than surfacing a raw ODataError stack trace to the LLM.
            # Docs: https://learn.microsoft.com/en-us/graph/api/plannerplan-list-buckets
            if exc.response_status_code != 404:
                raise
            raise ValueError(f"Plan '{plan_id}' not found.") from exc

    if ctx is not None:
        await ctx.info(f"Found {len(buckets)} bucket(s) in plan {plan_id}")

    return buckets or None


@buckets_router.tool(name="create_bucket")
async def create_bucket(
    plan_id: Annotated[
        str,
        Field(description="The plan ID to create the bucket in (from list_my_plans or list_group_plans)."),
    ],
    name: Annotated[
        str,
        Field(description="The display name for the new bucket.", min_length=1),
    ],
) -> dict | None:
    """Create a new bucket in a Planner plan.

    Args:
        plan_id: The ID of the plan to create the bucket in (from list_my_plans or list_group_plans).
        name: The display name for the new bucket.

    Returns:
        The created PlannerBucket object.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    ctx = get_optional_context()

    if ctx is not None:
        await ctx.info(f"Creating bucket '{name}' in plan {plan_id}")

    body = PlannerBucket()
    body.plan_id = plan_id
    body.name = name
    # orderHint " !" is the Graph API sentinel meaning "place first in order".
    # Without an orderHint, Graph rejects the POST with 400 Bad Request.
    # Docs: https://learn.microsoft.com/en-us/graph/api/resources/planner-order-hint-format
    body.order_hint = " !"

    async with graph_client_manager.for_user(token.token) as graph_client:
        try:
            bucket = await graph_client.planner.buckets.post(body)
        except ODataError as exc:
            # 403 means the user lacks write access to the plan. Convert to a
            # clear ValueError so the LLM receives a readable explanation.
            if exc.response_status_code != 403:
                raise
            code = exc.error.code if exc.error else None
            msg = exc.error.message if exc.error else exc.primary_message
            raise ValueError(f"Cannot create bucket ({code}): {msg}") from exc
    return BucketService.serialize_graph_object(bucket)


@buckets_router.tool(name="delete_bucket", annotations={"destructiveHint": True})
async def delete_bucket(
    bucket_id: Annotated[
        str,
        Field(description="The ID of the bucket to delete (from list_buckets)."),
    ],
    etag: Annotated[
        str,
        Field(
            description=(
                "The current @odata.etag of the bucket. Retries once automatically if "
                "the etag is stale (412 Precondition Failed). A 409 caused by a non-empty "
                "bucket is NOT retried — empty the bucket first."
            )
        ),
    ],
) -> dict:
    """Delete a Planner bucket.

    Note: the bucket must be empty (contain no tasks) before it can be deleted.
    Delete all tasks in the bucket first using delete_task, then call this tool.
    Attempting to delete a non-empty bucket returns a 409 Conflict from Graph;
    this is a permanent error — retrying will not help.
    Docs: https://learn.microsoft.com/en-us/graph/api/plannerbucket-delete

    Args:
        bucket_id: The ID of the bucket to delete (from list_buckets).
        etag: The current @odata.etag of the bucket. Retries once automatically if
            the etag is stale (412 Precondition Failed). A 409 caused by a non-empty
            bucket is NOT retried — empty the bucket first.

    Returns:
        A dict confirming deletion: {"deleted": true, "id": "<bucket_id>"}.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    async with graph_client_manager.for_user(token.token) as graph_client:
        # Pre-flight: check whether the bucket still contains tasks before
        # attempting deletion. The Graph API DELETE /planner/buckets/{id} docs
        # list 409 as a possible response, and live testing shows it can also
        # silently succeed on non-empty buckets, leaving tasks orphaned with a
        # dangling bucketId. Either outcome is harmful, so we fail-fast here.
        # We request only `id` with $top=1 — one field, one item is the cheapest
        # possible check to detect non-empty state without fetching all tasks.
        # Docs: https://learn.microsoft.com/en-us/graph/api/plannerbucket-list-tasks
        # Docs: https://learn.microsoft.com/en-us/graph/api/plannerbucket-delete
        task_check = await graph_client.planner.buckets.by_planner_bucket_id(bucket_id).tasks.get(
            request_configuration=RequestConfiguration(
                query_parameters=BucketTasksRequestBuilder.TasksRequestBuilderGetQueryParameters(
                    select=["id"],
                    top=1,
                )
            )
        )
        if task_check and task_check.value:
            raise ValueError(
                f"Bucket '{bucket_id}' still contains tasks. "
                "Delete all tasks in the bucket using delete_task first, then retry."
            )
        svc = BucketService(graph_client)
        await svc.delete_bucket(bucket_id, etag)
    return {"deleted": True, "id": bucket_id}
