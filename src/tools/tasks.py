from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_assignments import PlannerAssignments
from msgraph.generated.models.planner_checklist_items import PlannerChecklistItems
from msgraph.generated.models.planner_external_references import PlannerExternalReferences
from msgraph.generated.models.planner_task import PlannerTask
from msgraph.generated.models.planner_task_details import PlannerTaskDetails

from ..graph_client_manager import graph_client_manager
from ..services.planner_service import PlannerService

tasks_router = FastMCP("tasks")


@tasks_router.tool(name="list_my_tasks", annotations={"readOnlyHint": True})
async def list_my_tasks() -> list[PlannerTask] | None:
    """List all Planner tasks assigned to the authenticated user across all plans.

    Returns:
        A list of PlannerTask objects assigned to the user, or None if there are no tasks.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    async with graph_client_manager.for_user(token.token) as graph_client:
        tasks = await PlannerService.paginate(graph_client.me.planner.tasks)

    return tasks or None


@tasks_router.tool(name="get_task_details", annotations={"readOnlyHint": True})
async def get_task_details(task_id: str) -> PlannerTaskDetails | None:
    """Get the full details for a Planner task: description, checklist items, and external references.

    Args:
        task_id: The ID of the task to retrieve details for (from list_my_tasks or list_tasks).

    Returns:
        A PlannerTaskDetails object with description, checklist, and references.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    async with graph_client_manager.for_user(token.token) as graph_client:
        result = await graph_client.planner.tasks.by_planner_task_id(task_id).details.get()
    
    return result if result else None


@tasks_router.tool(name="update_task")
async def update_task(
    task_id: str,
    etag: str,
    title: str | None = None,
    percent_complete: int | None = None,
    due_date_time: str | None = None,
    bucket_id: str | None = None,
    assignee_priority: str | None = None,
    assign_user_ids: list[str] | None = None,
    unassign_user_ids: list[str] | None = None,
) -> PlannerTask | None:
    """Update a Planner task's basic fields. All fields are optional — only provided fields are changed.

    Args:
        task_id: The ID of the task to update (from list_my_tasks or list_tasks).
        etag: The current @odata.etag of the task. Retries once automatically if stale (412/409).
        title: New title for the task.
        percent_complete: Completion percentage from 0 to 100.
        due_date_time: ISO 8601 due date string (e.g. "2026-05-31T00:00:00").
        bucket_id: ID of the bucket to move the task to.
        assignee_priority: Order hint string for sorting within the assignee's task list.
        assign_user_ids: List of user object IDs to assign to the task.
        unassign_user_ids: List of user object IDs to remove from the task.

    Returns:
        The updated PlannerTask object.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    body = PlannerTask()
    for attr, value in [
        ("title", title),
        ("percent_complete", percent_complete),
        ("bucket_id", bucket_id),
        ("assignee_priority", assignee_priority),
        ("due_date_time", PlannerService.to_utc(due_date_time) if due_date_time is not None else None),
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
        svc = PlannerService(graph_client)
        return await svc.patch_task(task_id, body, etag)


@tasks_router.tool(name="update_task_details")
async def update_task_details(
    task_id: str,
    etag: str,
    description: str | None = None,
    checklist_items: dict | None = None,
    references: dict | None = None,
) -> PlannerTaskDetails | None:
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
        The updated PlannerTaskDetails object.
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
        svc = PlannerService(graph_client)
        try:
            return await svc.patch_task_details(task_id, body, etag)
        except ODataError as exc:
            if exc.response_status_code == 403:
                code = exc.error.code if exc.error else None
                msg = exc.error.message if exc.error else exc.primary_message
                raise ValueError(f"Cannot update task details ({code}): {msg}") from exc
            raise


@tasks_router.tool(name="delete_task")
async def delete_task(task_id: str, etag: str) -> None:
    """Delete a Planner task.

    Args:
        task_id: The ID of the task to delete.
        etag: The current @odata.etag of the task. Retries once automatically if stale (412/409).
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    async with graph_client_manager.for_user(token.token) as graph_client:
        svc = PlannerService(graph_client)
        await svc.delete_task(task_id, etag)
