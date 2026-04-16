"""Unit tests for tasks tool functions."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastmcp.exceptions import AuthorizationError
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_task import PlannerTask
from msgraph.generated.models.planner_task_details import PlannerTaskDetails

from src.services.base import BasePlannerService
from src.services.task_service import TaskService
from src.tools.tasks import (
    create_task,
    delete_task,
    get_task_details,
    list_my_tasks,
    list_tasks,
    update_task,
    update_task_details,
)

MODULE = "src.tools.tasks"
# MODULE is the import path patched by graph_ctx / token_capturing_ctx.
# It must be the module where get_access_token and graph_client_manager are
# *used* (i.e. imported into), not where they are defined. Patching the
# definition site would have no effect on the already-imported references.


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


def make_capturing_client() -> tuple[MagicMock, list]:
    """Returns (graph_client, captured_configs) where .get stores RequestConfigurations."""
    captured: list = []

    async def capturing_get(request_configuration=None):
        captured.append(request_configuration)
        return make_tasks_result([])

    client = MagicMock()
    client.me.planner.tasks.get = capturing_get
    return client, captured


def make_plan_capturing_client() -> tuple[MagicMock, list]:
    """Returns (graph_client, captured_configs) for the plan-scoped tasks endpoint."""
    captured: list = []

    async def capturing_get(request_configuration=None):
        captured.append(request_configuration)
        return make_tasks_result([])

    client = MagicMock()
    client.planner.plans.by_planner_plan_id.return_value.tasks.get = capturing_get
    return client, captured


# ---------------------------------------------------------------------------
# authorisation — all tools raise when no token
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("coro_fn", [
    lambda: list_my_tasks(),
    lambda: get_task_details("task-1"),
    lambda: update_task("task-1", '"etag-v1"', title="New Title"),
    lambda: update_task_details("task-1", '"etag-v1"', description="x"),
    lambda: delete_task("task-1", '"etag-v1"'),
    lambda: list_tasks("plan-1"),
    lambda: create_task("plan-1", "bucket-1", "My Task"),
], ids=["list-my-tasks", "get-task-details", "update-task", "update-task-details", "delete-task", "list-tasks", "create-task"])
async def test_no_token_raises(coro_fn):
    with patch(f"{MODULE}.get_access_token", return_value=None):
        with pytest.raises(AuthorizationError):
            await coro_fn()


# ---------------------------------------------------------------------------
# list_my_tasks
# ---------------------------------------------------------------------------


async def test_list_my_tasks_returns_tasks(graph_ctx):
    tasks = [make_task("task-1"), make_task("task-2")]
    graph_client = MagicMock()
    graph_client.me.planner.tasks.get = AsyncMock(return_value=make_tasks_result(tasks))

    with graph_ctx(MODULE, graph_client):
        result = await list_my_tasks()

    assert result == BasePlannerService.serialize_graph_list(tasks)


@pytest.mark.parametrize("get_return", [None, make_tasks_result(None)], ids=["result-none", "value-none"])
async def test_list_my_tasks_returns_none_when_empty(get_return, graph_ctx):
    graph_client = MagicMock()
    graph_client.me.planner.tasks.get = AsyncMock(return_value=get_return)

    with graph_ctx(MODULE, graph_client):
        result = await list_my_tasks()

    assert result is None


async def test_list_my_tasks_forwards_obo_token(token_capturing_ctx):
    graph_client = MagicMock()
    graph_client.me.planner.tasks.get = AsyncMock(return_value=make_tasks_result([]))

    with token_capturing_ctx(MODULE, graph_client, "my-obo") as received:
        await list_my_tasks()

    assert received == ["my-obo"]


@pytest.mark.parametrize("select_arg,expected", [
    ({"select": "id,title,planId"}, ["id", "title", "planId"]),
    ({}, ["id", "title", "planId", "bucketId", "percentComplete", "startDateTime", "dueDateTime", "assignments"]),
    ({"select": "*all"}, None),
    ({"select": None}, None),
], ids=["custom-csv", "default", "star-all", "explicit-none"])
async def test_list_my_tasks_select(select_arg, expected, graph_ctx):
    graph_client, captured = make_capturing_client()

    with graph_ctx(MODULE, graph_client):
        await list_my_tasks(**select_arg)

    assert captured[0].query_parameters.select == expected


# ---------------------------------------------------------------------------
# get_task_details
# ---------------------------------------------------------------------------


async def test_get_task_details_returns_details(graph_ctx):
    details = make_details()
    graph_client = MagicMock()
    graph_client.planner.tasks.by_planner_task_id.return_value.details.get = AsyncMock(return_value=details)

    with graph_ctx(MODULE, graph_client):
        result = await get_task_details("task-1")

    assert result == BasePlannerService.serialize_graph_object(details)
    graph_client.planner.tasks.by_planner_task_id.assert_called_once_with("task-1")


async def test_get_task_details_returns_none_when_not_found(graph_ctx):
    graph_client = MagicMock()
    graph_client.planner.tasks.by_planner_task_id.return_value.details.get = AsyncMock(return_value=None)

    with graph_ctx(MODULE, graph_client):
        result = await get_task_details("task-1")

    assert result is None


# ---------------------------------------------------------------------------
# update_task
# ---------------------------------------------------------------------------


async def test_update_task_returns_updated_task(graph_ctx):
    updated = make_task(title="Updated")
    svc = make_patch_svc(return_value=updated)

    with graph_ctx(MODULE, MagicMock()), patch(f"{MODULE}.TaskService", return_value=svc):
        result = await update_task("task-1", '"etag-v1"', title="Updated")

    assert result is updated
    svc.patch_task.assert_awaited_once()
    _, call_body, call_etag = svc.patch_task.call_args.args
    assert call_body.title == "Updated"
    assert call_etag == '"etag-v1"'


@pytest.mark.parametrize("field,value,attr", [
    ("percent_complete", 50, "percent_complete"),
    ("bucket_id", "bucket-abc", "bucket_id"),
    ("assignee_priority", "8585!", "assignee_priority"),
], ids=["percent-complete", "bucket-id", "assignee-priority"])
async def test_update_task_sets_scalar_fields(field, value, attr, graph_ctx):
    svc = make_patch_svc(return_value=make_task())

    with graph_ctx(MODULE, MagicMock()), patch(f"{MODULE}.TaskService", return_value=svc):
        await update_task("task-1", '"etag-v1"', **{field: value})

    _, body, _ = svc.patch_task.call_args.args
    assert getattr(body, attr) == value


@pytest.mark.parametrize("due_str,expected_utc", [
    ("2026-05-31T00:00:00", datetime(2026, 5, 31, 0, 0, 0, tzinfo=timezone.utc)),
    ("2026-05-31T10:00:00+05:30", datetime(2026, 5, 31, 4, 30, 0, tzinfo=timezone.utc)),
], ids=["naive-datetime-assumed-utc", "offset-datetime-converted-to-utc"])
async def test_update_task_converts_due_date_to_utc(due_str, expected_utc, graph_ctx):
    svc = make_patch_svc(return_value=make_task())

    with graph_ctx(MODULE, MagicMock()), \
         patch(f"{MODULE}.TaskService", return_value=svc) as mock_svc_cls:
        mock_svc_cls.to_utc = TaskService.to_utc
        await update_task("task-1", '"etag-v1"', due_date_time=due_str)

    _, body, _ = svc.patch_task.call_args.args
    assert body.due_date_time == expected_utc


async def test_update_task_assign_users_builds_correct_additional_data(graph_ctx):
    svc = make_patch_svc(return_value=make_task())

    with graph_ctx(MODULE, MagicMock()), patch(f"{MODULE}.TaskService", return_value=svc):
        await update_task("task-1", '"etag-v1"', assign_user_ids=["user-a"], unassign_user_ids=["user-b"])

    _, body, _ = svc.patch_task.call_args.args
    assert body.assignments.additional_data["user-a"] == {
        "@odata.type": "#microsoft.graph.plannerAssignment",
        "orderHint": " !",
    }
    assert body.assignments.additional_data["user-b"] is None


async def test_update_task_no_fields_sends_empty_body(graph_ctx):
    """Calling update_task with only required args sends an empty PlannerTask body."""
    svc = make_patch_svc(return_value=make_task())

    with graph_ctx(MODULE, MagicMock()), patch(f"{MODULE}.TaskService", return_value=svc):
        await update_task("task-1", '"etag-v1"')

    _, body, _ = svc.patch_task.call_args.args
    assert body.title is None
    assert body.assignments is None


# ---------------------------------------------------------------------------
# update_task_details
# ---------------------------------------------------------------------------


async def test_update_task_details_returns_updated_details(graph_ctx):
    updated = make_details("new desc")
    svc = make_patch_svc(return_value=updated)

    with graph_ctx(MODULE, MagicMock()), patch(f"{MODULE}.TaskService", return_value=svc):
        result = await update_task_details("task-1", '"etag-v1"', description="new desc")

    assert result is updated
    svc.patch_task_details.assert_awaited_once()
    _, body, etag = svc.patch_task_details.call_args.args
    assert body.description == "new desc"
    assert etag == '"etag-v1"'


async def test_update_task_details_sets_checklist_and_references(graph_ctx):
    svc = make_patch_svc(return_value=make_details())
    checklist = {"guid-1": {"@odata.type": "microsoft.graph.plannerChecklistItem", "title": "Step 1", "isChecked": False}}
    refs = {"https%3A//example%2Ecom": {"@odata.type": "microsoft.graph.plannerExternalReference", "alias": "Ref"}}

    with graph_ctx(MODULE, MagicMock()), patch(f"{MODULE}.TaskService", return_value=svc):
        await update_task_details("task-1", '"etag-v1"', checklist_items=checklist, references=refs)

    _, body, _ = svc.patch_task_details.call_args.args
    assert body.checklist.additional_data == checklist
    assert body.references.additional_data == refs


@pytest.mark.parametrize("status,code,exc_type", [
    (403, "MaximumChecklistItemsOnTask", ValueError),
    (400, "BadRequest", ODataError),
], ids=["403-value-error", "400-reraises"])
async def test_update_task_details_odata_error(status, code, exc_type, graph_ctx, make_odata_error):
    svc = make_patch_svc(side_effect=make_odata_error(status, code))

    with graph_ctx(MODULE, MagicMock()), patch(f"{MODULE}.TaskService", return_value=svc):
        with pytest.raises(exc_type) as exc_info:
            await update_task_details("task-1", '"etag-v1"', description="x")

    if exc_type is ValueError:
        assert code in str(exc_info.value)
    else:
        assert exc_info.value.response_status_code == status


# ---------------------------------------------------------------------------
# delete_task
# ---------------------------------------------------------------------------


async def test_delete_task_delegates_to_service(graph_ctx):
    svc = make_patch_svc()

    with graph_ctx(MODULE, MagicMock()), patch(f"{MODULE}.TaskService", return_value=svc):
        result = await delete_task("task-1", '"etag-v1"')

    svc.delete_task.assert_awaited_once_with("task-1", '"etag-v1"')
    assert result == {"deleted": True, "id": "task-1"}


# ---------------------------------------------------------------------------
# list_tasks
# ---------------------------------------------------------------------------


async def test_list_tasks_returns_tasks(graph_ctx):
    tasks = [make_task("t1"), make_task("t2")]
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.tasks.get = AsyncMock(
        return_value=make_tasks_result(tasks)
    )

    with graph_ctx(MODULE, graph_client):
        result = await list_tasks("plan-1")

    assert result == BasePlannerService.serialize_graph_list(tasks)
    graph_client.planner.plans.by_planner_plan_id.assert_called_once_with("plan-1")


@pytest.mark.parametrize("get_return", [None, make_tasks_result(None)], ids=["result-none", "value-none"])
async def test_list_tasks_returns_none_when_empty(get_return, graph_ctx):
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.tasks.get = AsyncMock(return_value=get_return)

    with graph_ctx(MODULE, graph_client):
        result = await list_tasks("plan-1")

    assert result is None


@pytest.mark.parametrize("select_arg,expected", [
    ({"select": "id,title"}, ["id", "title"]),
    ({}, ["id", "title", "planId", "bucketId", "percentComplete", "startDateTime", "dueDateTime", "assignments"]),
    ({"select": "*all"}, None),
    ({"select": None}, None),
], ids=["custom-csv", "default", "star-all", "explicit-none"])
async def test_list_tasks_select(select_arg, expected, graph_ctx):
    graph_client, captured = make_plan_capturing_client()

    with graph_ctx(MODULE, graph_client):
        await list_tasks("plan-1", **select_arg)

    assert captured[0].query_parameters.select == expected


# ---------------------------------------------------------------------------
# create_task
# ---------------------------------------------------------------------------


async def test_create_task_returns_created_task(graph_ctx):
    created = make_task("new-task", "My Task")
    graph_client = MagicMock()
    graph_client.planner.tasks.post = AsyncMock(return_value=created)

    with graph_ctx(MODULE, graph_client):
        result = await create_task("plan-1", "bucket-1", "My Task")

    assert result == BasePlannerService.serialize_graph_object(created)
    body = graph_client.planner.tasks.post.call_args.args[0]
    assert body.plan_id == "plan-1"
    assert body.bucket_id == "bucket-1"
    assert body.title == "My Task"


@pytest.mark.parametrize("kwarg,dt_str,expected_utc", [
    ("due_date_time",  "2026-05-31T00:00:00",       datetime(2026, 5, 31, 0,  0, 0, tzinfo=timezone.utc)),
    ("due_date_time",  "2026-05-31T10:00:00+05:30", datetime(2026, 5, 31, 4, 30, 0, tzinfo=timezone.utc)),
    ("start_date_time", "2026-05-01T00:00:00",       datetime(2026, 5,  1, 0,  0, 0, tzinfo=timezone.utc)),
    ("start_date_time", "2026-05-01T06:00:00+06:00", datetime(2026, 5,  1, 0,  0, 0, tzinfo=timezone.utc)),
], ids=["due-naive", "due-offset", "start-naive", "start-offset"])
async def test_create_task_converts_date_to_utc(kwarg, dt_str, expected_utc, graph_ctx):
    graph_client = MagicMock()
    graph_client.planner.tasks.post = AsyncMock(return_value=make_task())

    with graph_ctx(MODULE, graph_client):
        await create_task("plan-1", "bucket-1", "Task", **{kwarg: dt_str})

    body = graph_client.planner.tasks.post.call_args.args[0]
    assert getattr(body, kwarg) == expected_utc


async def test_create_task_raises_when_start_after_due(graph_ctx):
    with graph_ctx(MODULE, MagicMock()):
        with pytest.raises(ValueError, match="must not be after"):
            await create_task(
                "plan-1", "bucket-1", "Task",
                start_date_time="2026-06-01T00:00:00",
                due_date_time="2026-05-01T00:00:00",
            )


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


async def test_create_task_sets_optional_fields(graph_ctx):
    graph_client = MagicMock()
    graph_client.planner.tasks.post = AsyncMock(return_value=make_task())

    with graph_ctx(MODULE, graph_client):
        await create_task("plan-1", "bucket-1", "Task", percent_complete=50)

    body = graph_client.planner.tasks.post.call_args.args[0]
    assert body.percent_complete == 50


@pytest.mark.parametrize("status,code,exc_type", [
    (403, "MaximumTasksInProject", ValueError),
    (400, "BadRequest", ODataError),
], ids=["403-value-error", "400-reraises"])
async def test_create_task_odata_error(status, code, exc_type, graph_ctx, make_odata_error):
    graph_client = MagicMock()
    graph_client.planner.tasks.post = AsyncMock(side_effect=make_odata_error(status, code))

    with graph_ctx(MODULE, graph_client):
        with pytest.raises(exc_type) as exc_info:
            await create_task("plan-1", "bucket-1", "Task")

    if exc_type is ValueError:
        assert code in str(exc_info.value)
    else:
        assert exc_info.value.response_status_code == status


# ---------------------------------------------------------------------------
# Tests: Bug 015 — list_tasks 404 for invalid plan_id converts to ValueError
# ---------------------------------------------------------------------------


async def test_list_tasks_404_raises_value_error(graph_ctx, make_odata_error):
    # Bug 015: 404 from list_tasks with a non-existent plan_id was surfacing as
    # a raw 'Internal error'. It must now be a clear ValueError.
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.tasks.get = AsyncMock(
        side_effect=make_odata_error(404, "NotFound")
    )

    with graph_ctx(MODULE, graph_client):
        with pytest.raises(ValueError, match="not found"):
            await list_tasks("nonexistent-plan-id")


async def test_list_tasks_500_reraises(graph_ctx, make_odata_error):
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.tasks.get = AsyncMock(
        side_effect=make_odata_error(500, "ServiceError")
    )

    with graph_ctx(MODULE, graph_client):
        with pytest.raises(ODataError):
            await list_tasks("plan-1")


# ---------------------------------------------------------------------------
# Tests: Bug 016 — comma-only select resolves to zero fields after split
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_select", [", ,", ",", " , "], ids=["comma-space", "bare-comma", "space-comma-space"])
async def test_list_my_tasks_comma_only_select_raises_value_error(bad_select, graph_ctx):
    graph_client, _ = make_capturing_client()
    with graph_ctx(MODULE, graph_client):
        with pytest.raises(ValueError, match="resolved to no fields"):
            await list_my_tasks(select=bad_select)


@pytest.mark.parametrize("bad_select", [", ,", ",", " , "], ids=["comma-space", "bare-comma", "space-comma-space"])
async def test_list_tasks_comma_only_select_raises_value_error(bad_select, graph_ctx):
    graph_client, _ = make_plan_capturing_client()
    with graph_ctx(MODULE, graph_client):
        with pytest.raises(ValueError, match="resolved to no fields"):
            await list_tasks("plan-1", select=bad_select)
