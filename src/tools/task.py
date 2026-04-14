from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.headers_collection import HeadersCollection
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_assignments import PlannerAssignments
from msgraph.generated.models.planner_checklist_items import PlannerChecklistItems
from msgraph.generated.models.planner_external_references import PlannerExternalReferences
from msgraph.generated.models.planner_task import PlannerTask
from msgraph.generated.models.planner_task_details import PlannerTaskDetails

from ..graph_client_manager import graph_client_manager
from ..services.planner_service import PlannerService

task_router = FastMCP("task")


def _details_config(etag: str) -> RequestConfiguration:
    headers = HeadersCollection()
    headers.add("If-Match", etag)
    headers.add("Prefer", "return=representation")
    return RequestConfiguration(headers=headers)


@task_router.tool(name="get-task-details", annotations={"readOnlyHint": True})
async def get_task_details(task_id: str) -> PlannerTaskDetails | None:
    """Get the full details (description, checklist, references) for a Planner task.

    Requires the Tasks.Read delegated permission.
    """
    token = get_access_token()
    if token is None:
        raise ValueError("No access token available")

    graph_client = graph_client_manager.for_user(token.token)
    return await graph_client.planner.tasks.by_planner_task_id(task_id).details.get()


@task_router.tool(name="update-task")
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
    """Update a Planner task's basic fields.

    etag must be the current @odata.etag value for the task (from list-my-tasks or list-tasks).
    Automatically retries once if the ETag is stale (412/409).

    assign_user_ids: list of user object IDs to assign to the task.
    unassign_user_ids: list of user object IDs to remove from the task (sets assignment to null).

    Requires the Tasks.ReadWrite delegated permission.
    """
    token = get_access_token()
    if token is None:
        raise ValueError("No access token available")

    graph_client = graph_client_manager.for_user(token.token)
    svc = PlannerService(graph_client)

    body = PlannerTask()
    if title is not None:
        body.title = title
    if percent_complete is not None:
        body.percent_complete = percent_complete
    if bucket_id is not None:
        body.bucket_id = bucket_id
    if assignee_priority is not None:
        body.assignee_priority = assignee_priority
    if due_date_time is not None:
        from datetime import datetime, timezone
        body.due_date_time = datetime.fromisoformat(due_date_time).replace(tzinfo=timezone.utc)
    if assign_user_ids or unassign_user_ids:
        assignment_data: dict = {}
        for user_id in (assign_user_ids or []):
            assignment_data[user_id] = {
                "@odata_type": "#microsoft.graph.plannerAssignment",
                "order_hint": " !",
            }
        for user_id in (unassign_user_ids or []):
            assignment_data[user_id] = None
        body.assignments = PlannerAssignments(additional_data=assignment_data)

    return await svc.patch_task(task_id, body, etag)


@task_router.tool(name="update-task-details")
async def update_task_details(
    task_id: str,
    etag: str,
    description: str | None = None,
    checklist_items: dict | None = None,
    references: dict | None = None,
) -> PlannerTaskDetails | None:
    """Update the details of a Planner task: description, checklist, and external references.

    etag must be the current @odata.etag of the task *details* resource (from get-task-details).
    Automatically retries once if the ETag is stale (412/409).

    checklist_items: dict keyed by checklist item GUID:
        { "<guid>": { "@odata_type": "microsoft.graph.plannerChecklistItem",
                      "title": "...", "is_checked": false } }
        Pass null for a key to delete that checklist item.

    references: dict keyed by URL-encoded reference URL
        (periods → %2E, colons → %3A):
        { "https%3A//example%2Ecom": { "@odata_type": "microsoft.graph.plannerExternalReference",
                                       "alias": "...", "preview_priority": " !", "type": "Other" } }
        Pass null for a key to delete that reference.

    Raises ValueError on 403 MaximumChecklistItemsOnTask.
    Requires the Tasks.ReadWrite delegated permission.
    """
    token = get_access_token()
    if token is None:
        raise ValueError("No access token available")

    graph_client = graph_client_manager.for_user(token.token)

    body = PlannerTaskDetails()
    if description is not None:
        body.description = description
    if checklist_items is not None:
        body.checklist = PlannerChecklistItems(additional_data=checklist_items)
    if references is not None:
        body.references = PlannerExternalReferences(additional_data=references)

    async def _patch(e: str) -> PlannerTaskDetails | None:
        return await graph_client.planner.tasks.by_planner_task_id(
            task_id
        ).details.patch(body, request_configuration=_details_config(e))

    try:
        return await _patch(etag)
    except ODataError as exc:
        if exc.response_status_code == 403:
            code = exc.error.code if exc.error else None
            msg = exc.error.message if exc.error else exc.primary_message
            raise ValueError(f"Cannot update task details ({code}): {msg}") from exc
        if exc.response_status_code not in (409, 412):
            raise
        fresh = await graph_client.planner.tasks.by_planner_task_id(task_id).details.get()
        fresh_etag: str | None = fresh.additional_data.get("@odata.etag") if fresh else None
        if not fresh_etag:
            raise
        return await _patch(fresh_etag)


@task_router.tool(name="delete-task")
async def delete_task(task_id: str, etag: str) -> None:
    """Delete a Planner task.

    etag must be the current @odata.etag value for the task.
    Automatically retries once if the ETag is stale (412/409).
    Requires the Tasks.ReadWrite delegated permission.
    """
    token = get_access_token()
    if token is None:
        raise ValueError("No access token available")

    graph_client = graph_client_manager.for_user(token.token)
    svc = PlannerService(graph_client)
    await svc.delete_task(task_id, etag)
