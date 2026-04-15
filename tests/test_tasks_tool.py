"""Unit tests for tasks tool functions."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastmcp.exceptions import AuthorizationError
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_task import PlannerTask
from msgraph.generated.models.planner_task_details import PlannerTaskDetails

from src.tools.tasks import (
    delete_task,
    get_task_details,
    list_my_tasks,
    update_task,
    update_task_details,
)

MODULE = "src.tools.tasks"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_task(task_id: str = "task-1", title: str = "My Task", etag: str = '"etag-v1"') -> PlannerTask:
    task = PlannerTask()
    task.id = task_id
    task.title = title
    task.additional_data = {"@odata.etag": etag}
    return task


def make_details(description: str = "desc", etag: str = '"details-etag-v1"') -> PlannerTaskDetails:
    details = PlannerTaskDetails()
    details.description = description
    details.additional_data = {"@odata.etag": etag}
    return details


def make_tasks_result(tasks) -> MagicMock:
    result = MagicMock()
    result.value = tasks
    result.odata_next_link = None
    return result


def make_patch_svc(return_value=None, side_effect=None) -> MagicMock:
    svc = MagicMock()
    svc.patch_task = AsyncMock(return_value=return_value, side_effect=side_effect)
    svc.patch_task_details = AsyncMock(return_value=return_value, side_effect=side_effect)
    svc.delete_task = AsyncMock(return_value=None)
    return svc


# ---------------------------------------------------------------------------
# list_my_tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_my_tasks_no_token_raises():
    with patch(f"{MODULE}.get_access_token", return_value=None):
        with pytest.raises(AuthorizationError):
            await list_my_tasks()


@pytest.mark.asyncio
async def test_list_my_tasks_returns_tasks(graph_ctx):
    tasks = [make_task("task-1"), make_task("task-2")]
    graph_client = MagicMock()
    graph_client.me.planner.tasks.get = AsyncMock(return_value=make_tasks_result(tasks))

    with graph_ctx(MODULE, graph_client):
        result = await list_my_tasks()

    assert result == tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("get_return", [None, make_tasks_result(None)], ids=["result-none", "value-none"])
async def test_list_my_tasks_returns_none_when_empty(get_return, graph_ctx):
    graph_client = MagicMock()
    graph_client.me.planner.tasks.get = AsyncMock(return_value=get_return)

    with graph_ctx(MODULE, graph_client):
        result = await list_my_tasks()

    assert result is None


@pytest.mark.asyncio
async def test_list_my_tasks_forwards_obo_token(make_access_token):
    received: list[str] = []

    @asynccontextmanager
    async def _for_user(token: str):
        received.append(token)
        client = MagicMock()
        client.me.planner.tasks.get = AsyncMock(return_value=make_tasks_result([]))
        yield client

    with patch(f"{MODULE}.get_access_token", return_value=make_access_token("my-obo")), \
         patch(f"{MODULE}.graph_client_manager") as mock_mgr:
        mock_mgr.for_user = _for_user
        await list_my_tasks()

    assert received == ["my-obo"]


# ---------------------------------------------------------------------------
# get_task_details
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_task_details_no_token_raises():
    with patch(f"{MODULE}.get_access_token", return_value=None):
        with pytest.raises(AuthorizationError):
            await get_task_details("task-1")


@pytest.mark.asyncio
async def test_get_task_details_returns_details(graph_ctx):
    details = make_details()
    graph_client = MagicMock()
    graph_client.planner.tasks.by_planner_task_id.return_value.details.get = AsyncMock(return_value=details)

    with graph_ctx(MODULE, graph_client):
        result = await get_task_details("task-1")

    assert result is details
    graph_client.planner.tasks.by_planner_task_id.assert_called_once_with("task-1")


@pytest.mark.asyncio
async def test_get_task_details_returns_none_when_not_found(graph_ctx):
    graph_client = MagicMock()
    graph_client.planner.tasks.by_planner_task_id.return_value.details.get = AsyncMock(return_value=None)

    with graph_ctx(MODULE, graph_client):
        result = await get_task_details("task-1")

    assert result is None


# ---------------------------------------------------------------------------
# update_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_task_no_token_raises():
    with patch(f"{MODULE}.get_access_token", return_value=None):
        with pytest.raises(AuthorizationError):
            await update_task("task-1", '"etag-v1"', title="New Title")


@pytest.mark.asyncio
async def test_update_task_returns_updated_task(graph_ctx):
    updated = make_task(title="Updated")
    svc = make_patch_svc(return_value=updated)

    with graph_ctx(MODULE, MagicMock()), patch(f"{MODULE}.PlannerService", return_value=svc):
        result = await update_task("task-1", '"etag-v1"', title="Updated")

    assert result is updated
    svc.patch_task.assert_awaited_once()
    _, call_body, call_etag = svc.patch_task.call_args.args
    assert call_body.title == "Updated"
    assert call_etag == '"etag-v1"'


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value,attr", [
    ("percent_complete", 50, "percent_complete"),
    ("bucket_id", "bucket-abc", "bucket_id"),
    ("assignee_priority", "8585!", "assignee_priority"),
])
async def test_update_task_sets_scalar_fields(field, value, attr, graph_ctx):
    svc = make_patch_svc(return_value=make_task())

    with graph_ctx(MODULE, MagicMock()), patch(f"{MODULE}.PlannerService", return_value=svc):
        await update_task("task-1", '"etag-v1"', **{field: value})

    _, body, _ = svc.patch_task.call_args.args
    assert getattr(body, attr) == value


@pytest.mark.asyncio
@pytest.mark.parametrize("due_str,expected_utc", [
    ("2026-05-31T00:00:00", datetime(2026, 5, 31, 0, 0, 0, tzinfo=timezone.utc)),
    ("2026-05-31T10:00:00+05:30", datetime(2026, 5, 31, 4, 30, 0, tzinfo=timezone.utc)),
])
async def test_update_task_converts_due_date_to_utc(due_str, expected_utc, graph_ctx):
    svc = make_patch_svc(return_value=make_task())

    with graph_ctx(MODULE, MagicMock()), patch(f"{MODULE}.PlannerService", return_value=svc):
        await update_task("task-1", '"etag-v1"', due_date_time=due_str)

    _, body, _ = svc.patch_task.call_args.args
    assert body.due_date_time == expected_utc


@pytest.mark.asyncio
async def test_update_task_assign_users_builds_correct_additional_data(graph_ctx):
    svc = make_patch_svc(return_value=make_task())

    with graph_ctx(MODULE, MagicMock()), patch(f"{MODULE}.PlannerService", return_value=svc):
        await update_task("task-1", '"etag-v1"', assign_user_ids=["user-a"], unassign_user_ids=["user-b"])

    _, body, _ = svc.patch_task.call_args.args
    assert body.assignments.additional_data["user-a"] == {
        "@odata.type": "#microsoft.graph.plannerAssignment",
        "orderHint": " !",
    }
    assert body.assignments.additional_data["user-b"] is None


@pytest.mark.asyncio
async def test_update_task_no_fields_sends_empty_body(graph_ctx):
    """Calling update_task with only required args sends an empty PlannerTask body."""
    svc = make_patch_svc(return_value=make_task())

    with graph_ctx(MODULE, MagicMock()), patch(f"{MODULE}.PlannerService", return_value=svc):
        await update_task("task-1", '"etag-v1"')

    _, body, _ = svc.patch_task.call_args.args
    assert body.title is None
    assert body.assignments is None


# ---------------------------------------------------------------------------
# update_task_details
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_task_details_no_token_raises():
    with patch(f"{MODULE}.get_access_token", return_value=None):
        with pytest.raises(AuthorizationError):
            await update_task_details("task-1", '"etag-v1"', description="x")


@pytest.mark.asyncio
async def test_update_task_details_returns_updated_details(graph_ctx):
    updated = make_details("new desc")
    svc = make_patch_svc(return_value=updated)

    with graph_ctx(MODULE, MagicMock()), patch(f"{MODULE}.PlannerService", return_value=svc):
        result = await update_task_details("task-1", '"etag-v1"', description="new desc")

    assert result is updated
    svc.patch_task_details.assert_awaited_once()
    _, body, etag = svc.patch_task_details.call_args.args
    assert body.description == "new desc"
    assert etag == '"etag-v1"'


@pytest.mark.asyncio
async def test_update_task_details_sets_checklist_and_references(graph_ctx):
    svc = make_patch_svc(return_value=make_details())
    checklist = {"guid-1": {"@odata.type": "microsoft.graph.plannerChecklistItem", "title": "Step 1", "isChecked": False}}
    refs = {"https%3A//example%2Ecom": {"@odata.type": "microsoft.graph.plannerExternalReference", "alias": "Ref"}}

    with graph_ctx(MODULE, MagicMock()), patch(f"{MODULE}.PlannerService", return_value=svc):
        await update_task_details("task-1", '"etag-v1"', checklist_items=checklist, references=refs)

    _, body, _ = svc.patch_task_details.call_args.args
    assert body.checklist.additional_data == checklist
    assert body.references.additional_data == refs


@pytest.mark.asyncio
async def test_update_task_details_403_raises_value_error(graph_ctx, make_odata_error):
    svc = make_patch_svc(side_effect=make_odata_error(403, "MaximumChecklistItemsOnTask", "Too many items"))

    with graph_ctx(MODULE, MagicMock()), patch(f"{MODULE}.PlannerService", return_value=svc):
        with pytest.raises(ValueError, match="MaximumChecklistItemsOnTask"):
            await update_task_details("task-1", '"etag-v1"', description="x")


@pytest.mark.asyncio
async def test_update_task_details_non_403_odata_error_reraises(graph_ctx, make_odata_error):
    svc = make_patch_svc(side_effect=make_odata_error(400, "BadRequest"))

    with graph_ctx(MODULE, MagicMock()), patch(f"{MODULE}.PlannerService", return_value=svc):
        with pytest.raises(ODataError) as exc_info:
            await update_task_details("task-1", '"etag-v1"', description="x")

    assert exc_info.value.response_status_code == 400


# ---------------------------------------------------------------------------
# delete_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_task_no_token_raises():
    with patch(f"{MODULE}.get_access_token", return_value=None):
        with pytest.raises(AuthorizationError):
            await delete_task("task-1", '"etag-v1"')


@pytest.mark.asyncio
async def test_delete_task_delegates_to_service(graph_ctx):
    svc = make_patch_svc()

    with graph_ctx(MODULE, MagicMock()), patch(f"{MODULE}.PlannerService", return_value=svc):
        await delete_task("task-1", '"etag-v1"')

    svc.delete_task.assert_awaited_once_with("task-1", '"etag-v1"')


@pytest.mark.asyncio
async def test_delete_task_returns_none(graph_ctx):
    svc = make_patch_svc()

    with graph_ctx(MODULE, MagicMock()), patch(f"{MODULE}.PlannerService", return_value=svc):
        result = await delete_task("task-1", '"etag-v1"')

    assert result is None
