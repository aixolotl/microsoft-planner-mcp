from __future__ import annotations

from typing import Annotated

from pydantic import Field

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_assignments import PlannerAssignments
from msgraph.generated.models.planner_checklist_items import PlannerChecklistItems
from msgraph.generated.models.planner_external_references import PlannerExternalReferences
from msgraph.generated.models.planner_task import PlannerTask
from msgraph.generated.models.planner_task_details import PlannerTaskDetails
# Two different SDK-generated request builders share the class name
# "TasksRequestBuilder" but live in different URL namespaces:
#   - users/.../planner/tasks  (used by list_my_tasks — tasks assigned to me)
#   - planner/plans/{id}/tasks (used by list_tasks — tasks within a plan)
# Both are imported so we can build the correct $select query parameters for
# each endpoint. The alias avoids a name collision at import time.
from msgraph.generated.users.item.planner.tasks.tasks_request_builder import TasksRequestBuilder
from msgraph.generated.planner.plans.item.tasks.tasks_request_builder import TasksRequestBuilder as PlanTasksRequestBuilder

from ..deps import get_optional_context
from ..graph_client_manager import graph_client_manager
from ..services.task_service import TaskService

tasks_router = FastMCP("tasks")


# readOnlyHint=True signals to MCP clients that this tool never mutates state,
# allowing them to skip user confirmation prompts for read operations.
# Docs: https://gofastmcp.com/servers/tools#using-annotation-hints
@tasks_router.tool(name="list_my_tasks", annotations={"readOnlyHint": True})
async def list_my_tasks(
    select: str | None = "id,title,planId,bucketId,percentComplete,dueDateTime,assignments",
) -> list[dict] | None:
    """List all Planner tasks assigned to the authenticated user across all plans.

    Args:
        select: Comma-separated list of PlannerTask fields to include. Default is "id,title,planId,bucketId,percentComplete,dueDateTime,assignments". Pass "*all" for all fields.

    Returns:
        A list of PlannerTask objects assigned to the user, or None if there are no tasks.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    ctx = get_optional_context()

    if ctx is not None:
        await ctx.info("Fetching tasks assigned to the authenticated user")

    # "*all" is a sentinel that tells the tool to omit $select entirely so
    # Graph returns every task field. Without the sentinel, callers would have
    # to set select=None explicitly, which is less ergonomic for LLM agents.
    if select == "*all":
        select = None

    # The Graph SDK's $select parameter is a list[str], not a comma-separated
    # string. We split here so callers can use the natural "id,title" syntax.
    select_fields = (
        [field.strip() for field in select.split(",") if field.strip()]
        if select is not None
        else None
    )

    async with graph_client_manager.for_user(token.token) as graph_client:
        tasks = await TaskService(graph_client, serialize=True).paginate(
            graph_client.me.planner.tasks,
            RequestConfiguration(
                query_parameters=TasksRequestBuilder.TasksRequestBuilderGetQueryParameters(
                    select=select_fields
                )
            ),
        )

    if ctx is not None:
        await ctx.info(f"Found {len(tasks)} task(s) assigned to you")

    return tasks or None


# readOnlyHint=True: this tool only reads from Graph, never writes.
# Docs: https://gofastmcp.com/servers/tools#using-annotation-hints
@tasks_router.tool(name="list_tasks", annotations={"readOnlyHint": True})
async def list_tasks(
    plan_id: str,
    select: str | None = "id,title,planId,bucketId,percentComplete,dueDateTime,assignments",
) -> list[dict] | None:
    """List all tasks in a Planner plan.

    Args:
        plan_id: The ID of the plan to list tasks for (from list_my_plans or list_group_plans).
        select: Comma-separated list of PlannerTask fields to include. Default is "id,title,planId,bucketId,percentComplete,dueDateTime,assignments". Pass "*all" for all fields.

    Returns:
        A list of PlannerTask objects in the plan, or None if the plan has no tasks.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    ctx = get_optional_context()

    if ctx is not None:
        await ctx.info(f"Fetching tasks for plan {plan_id}")

    if select == "*all":
        select = None

    select_fields = (
        [field.strip() for field in select.split(",") if field.strip()]
        if select is not None
        else None
    )

    async with graph_client_manager.for_user(token.token) as graph_client:
        tasks = await TaskService(graph_client, serialize=True).paginate(
            graph_client.planner.plans.by_planner_plan_id(plan_id).tasks,
            RequestConfiguration(
                query_parameters=PlanTasksRequestBuilder.TasksRequestBuilderGetQueryParameters(
                    select=select_fields
                )
            ),
        )

    if ctx is not None:
        await ctx.info(f"Found {len(tasks)} task(s) in plan {plan_id}")

    return tasks or None


@tasks_router.tool(name="create_task")
async def create_task(
    plan_id: str,
    bucket_id: str,
    title: str,
    start_date_time: str | None = None,
    due_date_time: str | None = None,
    percent_complete: Annotated[int, Field(ge=0, le=100)] | None = None,
    assign_user_ids: list[str] | None = None,
) -> dict | None:
    """Create a new task in a Planner plan.

    Args:
        plan_id: The ID of the plan to create the task in.
        bucket_id: The ID of the bucket to place the task in (from list_buckets).
        title: The title of the task.
        start_date_time: Optional ISO 8601 start date (e.g. "2026-05-01T00:00:00"). Must not be after due_date_time.
        due_date_time: Optional ISO 8601 due date (e.g. "2026-05-31T00:00:00").
        percent_complete: Optional completion percentage (0–100).
        assign_user_ids: Optional list of user object IDs to assign to the task.

    Returns:
        The created task as a dict with non-null fields.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    start_dt = TaskService.to_utc(start_date_time) if start_date_time else None
    due_dt = TaskService.to_utc(due_date_time) if due_date_time else None
    if start_dt and due_dt and start_dt > due_dt:
        raise ValueError(f"start_date_time ({start_date_time}) must not be after due_date_time ({due_date_time})")

    body = PlannerTask()
    body.plan_id = plan_id
    body.bucket_id = bucket_id
    body.title = title
    if start_dt is not None:
        body.start_date_time = start_dt
    if due_dt is not None:
        body.due_date_time = due_dt
    if percent_complete is not None:
        body.percent_complete = percent_complete
    if assign_user_ids:
        body.assignments = PlannerAssignments(
            additional_data={
                user_id: {
                    "@odata.type": "#microsoft.graph.plannerAssignment",
                    # orderHint " !" is the Graph API sentinel meaning
                    # "place at the top of the order". It is a magic string
                    # defined by the Planner ordering algorithm; without it
                    # the API rejects the assignment with a 400 BadRequest.
                    # Docs: https://learn.microsoft.com/en-us/graph/api/resources/planner-order-hint-format
                    "orderHint": " !",
                }
                for user_id in assign_user_ids
            }
        )

    async with graph_client_manager.for_user(token.token) as graph_client:
        try:
            task = await graph_client.planner.tasks.post(body)
        except ODataError as exc:
            # 403 from task creation usually means the user has hit the plan
            # task limit or lacks write permission. Convert to ValueError with a
            # readable message so the LLM gets a clear explanation. All other
            # status codes are re-raised so genuine failures surface normally.
            if exc.response_status_code != 403:
                raise
            code = exc.error.code if exc.error else None
            msg = exc.error.message if exc.error else exc.primary_message
            raise ValueError(f"Cannot create task ({code}): {msg}") from exc
    return TaskService.serialize_graph_object(task)


# readOnlyHint=True: this tool reads task details without any side effects.
# Docs: https://gofastmcp.com/servers/tools#using-annotation-hints
@tasks_router.tool(name="get_task_details", annotations={"readOnlyHint": True})
async def get_task_details(task_id: str) -> dict | None:
    """Get the full details for a Planner task: description, checklist items, and external references.

    Args:
        task_id: The ID of the task to retrieve details for (from list_my_tasks or list_tasks).

    Returns:
        The task details as a dict with non-null fields.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    ctx = get_optional_context()

    if ctx is not None:
        await ctx.debug(f"Fetching details for task {task_id}")

    async with graph_client_manager.for_user(token.token) as graph_client:
        result = await graph_client.planner.tasks.by_planner_task_id(task_id).details.get()

    return TaskService.serialize_graph_object(result)


@tasks_router.tool(name="update_task")
async def update_task(
    task_id: str,
    etag: str,
    title: str | None = None,
    percent_complete: Annotated[int, Field(ge=0, le=100)] | None = None,
    start_date_time: str | None = None,
    due_date_time: str | None = None,
    bucket_id: str | None = None,
    assignee_priority: str | None = None,
    assign_user_ids: list[str] | None = None,
    unassign_user_ids: list[str] | None = None,
) -> dict | None:
    """Update a Planner task's basic fields. All fields are optional — only provided fields are changed.

    Args:
        task_id: The ID of the task to update (from list_my_tasks or list_tasks).
        etag: The current @odata.etag of the task. Retries once automatically if stale (412/409).
        title: New title for the task.
        percent_complete: Completion percentage from 0 to 100.
        start_date_time: ISO 8601 start date string (e.g. "2026-05-01T00:00:00"). Must not be after due_date_time.
        due_date_time: ISO 8601 due date string (e.g. "2026-05-31T00:00:00").
        bucket_id: ID of the bucket to move the task to.
        assignee_priority: Order hint string for sorting within the assignee's task list.
        assign_user_ids: List of user object IDs to assign to the task.
        unassign_user_ids: List of user object IDs to remove from the task.

    Returns:
        The updated task as a dict with non-null fields.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    # Detect conflicting user lists before touching the Graph API so the caller
    # gets a clear error rather than a silent no-op where the unassign wins.
    # Docs: https://learn.microsoft.com/en-us/graph/api/plannertask-update
    if assign_user_ids and unassign_user_ids:
        conflict = set(assign_user_ids) & set(unassign_user_ids)
        if conflict:
            raise ValueError(f"User IDs present in both assign_user_ids and unassign_user_ids: {sorted(conflict)}")

    start_dt = TaskService.to_utc(start_date_time) if start_date_time else None
    due_dt = TaskService.to_utc(due_date_time) if due_date_time else None
    # Validate ordering only when both are being set in this call; if only one
    # is provided the existing stored value is unchanged and we cannot check here.
    # Docs: https://learn.microsoft.com/en-us/graph/api/resources/plannertask
    if start_dt and due_dt and start_dt > due_dt:
        raise ValueError(f"start_date_time ({start_date_time}) must not be after due_date_time ({due_date_time})")

    body = PlannerTask()
    for attr, value in [
        ("title", title),
        ("percent_complete", percent_complete),
        ("bucket_id", bucket_id),
        ("assignee_priority", assignee_priority),
        ("start_date_time", start_dt),
        ("due_date_time", due_dt),
    ]:
        if value is not None:
            setattr(body, attr, value)
    if assign_user_ids or unassign_user_ids:
        assignment_data: dict = {
            user_id: {"@odata.type": "#microsoft.graph.plannerAssignment", "orderHint": " !"}
            for user_id in (assign_user_ids or [])
        } | {user_id: None for user_id in (unassign_user_ids or [])}
        body.assignments = PlannerAssignments(additional_data=assignment_data)

    async with graph_client_manager.for_user(token.token) as graph_client:
        svc = TaskService(graph_client, serialize=True)
        task = await svc.patch_task(task_id, body, etag)
    return task  # type: ignore[return-value]


@tasks_router.tool(name="update_task_details")
async def update_task_details(
    task_id: str,
    etag: str,
    description: str | None = None,
    checklist_items: dict | None = None,
    references: dict | None = None,
) -> dict | None:
    """Update the details of a Planner task: description, checklist items, and external references.

    Args:
        task_id: The ID of the task to update.
        etag: The current @odata.etag of the task details resource (from get_task_details). Retries once automatically if stale (412/409).
        description: New plain-text description for the task.
        checklist_items: Dict keyed by checklist item GUID. Pass null for a key to delete that item.
            Example: { "<guid>": { "@odata.type": "microsoft.graph.plannerChecklistItem", "title": "...", "isChecked": false } }
        references: Dict keyed by URL-encoded reference URL (periods → %2E, colons → %3A). Pass null for a key to delete that reference.
            Example: { "https%3A//example%2Ecom": { "@odata.type": "microsoft.graph.plannerExternalReference", "alias": "...", "previewPriority": " !", "type": "Other" } }

    Returns:
        The updated task details as a dict with non-null fields.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    body = PlannerTaskDetails()
    if description is not None:
        body.description = description
    if checklist_items is not None:
        body.checklist = PlannerChecklistItems(additional_data=checklist_items)
    if references is not None:
        body.references = PlannerExternalReferences(additional_data=references)

    async with graph_client_manager.for_user(token.token) as graph_client:
        svc = TaskService(graph_client, serialize=True)
        try:
            details = await svc.patch_task_details(task_id, body, etag)
        except ODataError as exc:
            if exc.response_status_code == 403:
                code = exc.error.code if exc.error else None
                msg = exc.error.message if exc.error else exc.primary_message
                raise ValueError(f"Cannot update task details ({code}): {msg}") from exc
            raise
    return details  # type: ignore[return-value]


@tasks_router.tool(name="delete_task")
async def delete_task(task_id: str, etag: str) -> dict:
    """Delete a Planner task.

    Args:
        task_id: The ID of the task to delete.
        etag: The current @odata.etag of the task. Retries once automatically if stale (412/409).

    Returns:
        A dict confirming deletion: {"deleted": true, "id": "<task_id>"}.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    async with graph_client_manager.for_user(token.token) as graph_client:
        svc = TaskService(graph_client)
        await svc.delete_task(task_id, etag)
    return {"deleted": True, "id": task_id}
