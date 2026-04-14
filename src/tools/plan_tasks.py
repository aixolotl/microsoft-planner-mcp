from __future__ import annotations

from datetime import datetime, timezone

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_assignments import PlannerAssignments
from msgraph.generated.models.planner_task import PlannerTask

from ..graph_client_manager import graph_client_manager

plan_tasks_router = FastMCP("plan_tasks")


@plan_tasks_router.tool(name="list-tasks", annotations={"readOnlyHint": True})
async def list_tasks(plan_id: str) -> list[PlannerTask]:
    """List all tasks in a Planner plan.

    Requires the Tasks.Read delegated permission.
    """
    token = get_access_token()
    if token is None:
        raise ValueError("No access token available")

    graph_client = graph_client_manager.for_user(token.token)
    result = await graph_client.planner.plans.by_planner_plan_id(plan_id).tasks.get()

    tasks = result.value if result and result.value else []
    return sorted(tasks, key=lambda t: t.order_hint or "")


@plan_tasks_router.tool(name="create-task")
async def create_task(
    plan_id: str,
    bucket_id: str,
    title: str,
    start_date_time: str | None = None,
    due_date_time: str | None = None,
    percent_complete: int | None = None,
    assign_user_ids: list[str] | None = None,
) -> PlannerTask | None:
    """Create a new task in a Planner plan.

    assign_user_ids: optional list of user object IDs to assign to the task.
    start_date_time / due_date_time: ISO 8601 strings (e.g. "2026-05-01T00:00:00").
    Raises ValueError if start_date_time > due_date_time.
    Raises ODataError on 403 if the user is not a member of the plan's group
    or a Planner task limit has been reached.
    Requires the Tasks.ReadWrite delegated permission.
    """
    token = get_access_token()
    if token is None:
        raise ValueError("No access token available")

    start_dt = datetime.fromisoformat(start_date_time).replace(tzinfo=timezone.utc) if start_date_time else None
    due_dt = datetime.fromisoformat(due_date_time).replace(tzinfo=timezone.utc) if due_date_time else None
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
                    "@odata_type": "#microsoft.graph.plannerAssignment",
                    "order_hint": " !",
                }
                for user_id in assign_user_ids
            }
        )

    graph_client = graph_client_manager.for_user(token.token)
    try:
        return await graph_client.planner.tasks.post(body)
    except ODataError as exc:
        if exc.response_status_code == 403:
            code = exc.error.code if exc.error else None
            msg = exc.error.message if exc.error else exc.primary_message
            raise ValueError(f"Cannot create task ({code}): {msg}") from exc
        raise
