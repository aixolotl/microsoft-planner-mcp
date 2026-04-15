"""Unit tests for list_tasks and create_task in plans.py."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastmcp.exceptions import AuthorizationError
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_task import PlannerTask

from src.tools.plans import create_task, list_tasks

MODULE = "src.tools.plans"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_task(task_id: str = "task-1", title: str = "My Task") -> PlannerTask:
    task = PlannerTask()
    task.id = task_id
    task.title = title
    return task


def make_tasks_result(tasks) -> MagicMock:
    result = MagicMock()
    result.value = tasks
    result.odata_next_link = None
    return result


# ---------------------------------------------------------------------------
# list_tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tasks_no_token_raises():
    with patch(f"{MODULE}.get_access_token", return_value=None):
        with pytest.raises(AuthorizationError):
            await list_tasks("plan-1")


@pytest.mark.asyncio
async def test_list_tasks_returns_tasks(graph_ctx):
    tasks = [make_task("t1"), make_task("t2")]
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.tasks.get = AsyncMock(
        return_value=make_tasks_result(tasks)
    )

    with graph_ctx(MODULE, graph_client):
        result = await list_tasks("plan-1")

    assert result == tasks
    graph_client.planner.plans.by_planner_plan_id.assert_called_once_with("plan-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("get_return", [None, make_tasks_result(None)], ids=["result-none", "value-none"])
async def test_list_tasks_returns_none_when_empty(get_return, graph_ctx):
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.tasks.get = AsyncMock(return_value=get_return)

    with graph_ctx(MODULE, graph_client):
        result = await list_tasks("plan-1")

    assert result is None


# ---------------------------------------------------------------------------
# create_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_task_no_token_raises():
    with patch(f"{MODULE}.get_access_token", return_value=None):
        with pytest.raises(AuthorizationError):
            await create_task("plan-1", "bucket-1", "My Task")


@pytest.mark.asyncio
async def test_create_task_returns_created_task(graph_ctx):
    created = make_task("new-task", "My Task")
    graph_client = MagicMock()
    graph_client.planner.tasks.post = AsyncMock(return_value=created)

    with graph_ctx(MODULE, graph_client):
        result = await create_task("plan-1", "bucket-1", "My Task")

    assert result is created
    body = graph_client.planner.tasks.post.call_args.args[0]
    assert body.plan_id == "plan-1"
    assert body.bucket_id == "bucket-1"
    assert body.title == "My Task"


@pytest.mark.asyncio
@pytest.mark.parametrize("due_str,expected_utc", [
    ("2026-05-31T00:00:00", datetime(2026, 5, 31, 0, 0, 0, tzinfo=timezone.utc)),
    ("2026-05-31T10:00:00+05:30", datetime(2026, 5, 31, 4, 30, 0, tzinfo=timezone.utc)),
])
async def test_create_task_converts_due_date_to_utc(due_str, expected_utc, graph_ctx):
    graph_client = MagicMock()
    graph_client.planner.tasks.post = AsyncMock(return_value=make_task())

    with graph_ctx(MODULE, graph_client):
        await create_task("plan-1", "bucket-1", "Task", due_date_time=due_str)

    body = graph_client.planner.tasks.post.call_args.args[0]
    assert body.due_date_time == expected_utc


@pytest.mark.asyncio
@pytest.mark.parametrize("start_str,expected_utc", [
    ("2026-05-01T00:00:00", datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)),
    ("2026-05-01T06:00:00+06:00", datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)),
])
async def test_create_task_converts_start_date_to_utc(start_str, expected_utc, graph_ctx):
    graph_client = MagicMock()
    graph_client.planner.tasks.post = AsyncMock(return_value=make_task())

    with graph_ctx(MODULE, graph_client):
        await create_task("plan-1", "bucket-1", "Task", start_date_time=start_str)

    body = graph_client.planner.tasks.post.call_args.args[0]
    assert body.start_date_time == expected_utc


@pytest.mark.asyncio
async def test_create_task_raises_when_start_after_due(graph_ctx):
    with graph_ctx(MODULE, MagicMock()):
        with pytest.raises(ValueError, match="must not be after"):
            await create_task(
                "plan-1", "bucket-1", "Task",
                start_date_time="2026-06-01T00:00:00",
                due_date_time="2026-05-01T00:00:00",
            )


@pytest.mark.asyncio
async def test_create_task_assigns_users(graph_ctx):
    graph_client = MagicMock()
    graph_client.planner.tasks.post = AsyncMock(return_value=make_task())

    with graph_ctx(MODULE, graph_client):
        await create_task("plan-1", "bucket-1", "Task", assign_user_ids=["user-a", "user-b"])

    body = graph_client.planner.tasks.post.call_args.args[0]
    assert body.assignments.additional_data["user-a"] == {
        "@odata.type": "#microsoft.graph.plannerAssignment",
        "orderHint": " !",
    }
    assert body.assignments.additional_data["user-b"] == {
        "@odata.type": "#microsoft.graph.plannerAssignment",
        "orderHint": " !",
    }


@pytest.mark.asyncio
async def test_create_task_sets_optional_fields(graph_ctx):
    graph_client = MagicMock()
    graph_client.planner.tasks.post = AsyncMock(return_value=make_task())

    with graph_ctx(MODULE, graph_client):
        await create_task("plan-1", "bucket-1", "Task", percent_complete=50)

    body = graph_client.planner.tasks.post.call_args.args[0]
    assert body.percent_complete == 50


@pytest.mark.asyncio
async def test_create_task_403_raises_value_error(graph_ctx, make_odata_error):
    graph_client = MagicMock()
    graph_client.planner.tasks.post = AsyncMock(side_effect=make_odata_error(403, "MaximumTasksInProject"))

    with graph_ctx(MODULE, graph_client):
        with pytest.raises(ValueError, match="MaximumTasksInProject"):
            await create_task("plan-1", "bucket-1", "Task")


@pytest.mark.asyncio
async def test_create_task_non_403_odata_error_reraises(graph_ctx, make_odata_error):
    graph_client = MagicMock()
    graph_client.planner.tasks.post = AsyncMock(side_effect=make_odata_error(400, "BadRequest"))

    with graph_ctx(MODULE, graph_client):
        with pytest.raises(ODataError) as exc_info:
            await create_task("plan-1", "bucket-1", "Task")

    assert exc_info.value.response_status_code == 400
