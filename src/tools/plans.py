from __future__ import annotations

from datetime import datetime, timezone

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_assignments import PlannerAssignments
from msgraph.generated.models.planner_plan import PlannerPlan
from msgraph.generated.models.planner_task import PlannerTask

from ..graph_client_manager import graph_client_manager
from ..services.planner_service import PlannerService

plans_router = FastMCP("plans")


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

    if select == "*all":
        select = None  # No need to specify $select if we want all fields

    select_fields = select.split(",") if select else None

    async with graph_client_manager.for_user(token.token) as graph_client:
        service = PlannerService(graph_client)
        all_plans = await service.list_my_plans(select=select_fields)

    return all_plans or None


@plans_router.tool(name="list_tasks", annotations={"readOnlyHint": True})
async def list_tasks(plan_id: str) -> list[PlannerTask] | None:
    """List all tasks in a Planner plan.

    Args:
        plan_id: The ID of the plan to list tasks for (from list_my_plans or list_group_plans).

    Returns:
        A list of PlannerTask objects in the plan, or None if the plan has no tasks.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    async with graph_client_manager.for_user(token.token) as graph_client:
        result = await graph_client.planner.plans.by_planner_plan_id(plan_id).tasks.get()

    return result.value if result else None


@plans_router.tool(name="create_task")
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

    Args:
        plan_id: The ID of the plan to create the task in.
        bucket_id: The ID of the bucket to place the task in (from list_buckets).
        title: The title of the task.
        start_date_time: Optional ISO 8601 start date (e.g. "2026-05-01T00:00:00"). Must not be after due_date_time.
        due_date_time: Optional ISO 8601 due date (e.g. "2026-05-31T00:00:00").
        percent_complete: Optional completion percentage (0–100).
        assign_user_ids: Optional list of user object IDs to assign to the task.

    Returns:
        The created PlannerTask object.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    def _to_utc(s: str) -> datetime:
        dt = datetime.fromisoformat(s)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)

    start_dt = _to_utc(start_date_time) if start_date_time else None
    due_dt = _to_utc(due_date_time) if due_date_time else None
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
                    "orderHint": " !",
                }
                for user_id in assign_user_ids
            }
        )

    async with graph_client_manager.for_user(token.token) as graph_client:
        try:
            return await graph_client.planner.tasks.post(body)
        except ODataError as exc:
            if exc.response_status_code != 403:
                raise
            code = exc.error.code if exc.error else None
            msg = exc.error.message if exc.error else exc.primary_message
            raise ValueError(f"Cannot create task ({code}): {msg}") from exc
