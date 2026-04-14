from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_access_token
from msgraph.generated.models.planner_task import PlannerTask

from ..graph_client_manager import graph_client_manager

tasks_router = FastMCP("tasks")


@tasks_router.tool(name="list-my-tasks", annotations={"readOnlyHint": True})
async def list_my_tasks() -> list[PlannerTask]:
    """List all Planner tasks assigned to the authenticated user across all plans.

    Results are sorted by assigneePriority.
    Requires the Tasks.Read delegated permission.
    """
    token = get_access_token()
    if token is None:
        raise ValueError("No access token available")

    graph_client = graph_client_manager.for_user(token.token)
    result = await graph_client.me.planner.tasks.get()

    tasks = result.value if result and result.value else []
    return sorted(tasks, key=lambda t: t.assignee_priority or "")
