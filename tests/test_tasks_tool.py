"""Unit tests for tasks tool functions."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastmcp.exceptions import AuthorizationError
from msgraph.generated.models.planner_task import PlannerTask
from msgraph.generated.models.planner_task_details import PlannerTaskDetails

from src.tools.tasks import (
    create_task,
    delete_task,
    get_task_details,
    list_task_fields,
    list_my_tasks,
    list_tasks,
    update_task,
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


def make_patching_graph_client(return_value=None, side_effect=None, details_return_value=None, details_side_effect=None) -> MagicMock:
    """Build a mock graph_client whose task/details builders return the given value.

    The real PlannerService is instantiated by the tool (not mocked), so we
    configure the graph_client's SDK builders to return the desired result.
    details_return_value / details_side_effect control the details PATCH.
    When details_return_value is not explicitly provided it falls back to
    return_value for backwards compatibility with existing tests.
    """
    client = MagicMock()
    # task-level PATCH
    client.planner.tasks.by_planner_task_id.return_value.patch = AsyncMock(
        return_value=return_value, side_effect=side_effect
    )
    # task details PATCH — falls back to the top-level return_value when
    # no details-specific value is provided, matching legacy behaviour.
    client.planner.tasks.by_planner_task_id.return_value.details.patch = AsyncMock(
        return_value=details_return_value if details_return_value is not None else return_value,
        side_effect=details_side_effect if details_side_effect is not None else side_effect,
    )
    # task details GET — needed for auto-refresh of etagDetails when not
    # provided by the caller. Returns an object with an @odata.etag in
    # additional_data so svc.refresh_etag() succeeds.
    _details_get_obj = MagicMock()
    _details_get_obj.additional_data = {"@odata.etag": '"auto-details-etag"'}
    client.planner.tasks.by_planner_task_id.return_value.details.get = AsyncMock(
        return_value=_details_get_obj
    )
    # task DELETE
    client.planner.tasks.by_planner_task_id.return_value.delete = AsyncMock(return_value=None)
    return client


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
    lambda: update_task("task-1", '"etag-v1"', description="x"),
    lambda: delete_task("task-1", '"etag-v1"'),
    lambda: list_tasks("plan-1"),
    lambda: create_task("plan-1", "bucket-1", "My Task"),
], ids=["list-my-tasks", "get-task-details", "update-task", "update-task-details-fields", "delete-task", "list-tasks", "create-task"])
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

    assert result == tasks


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
    ({}, None),
    ({"select": "*all"}, None),
    ({"select": None}, None),
], ids=["custom-csv", "default", "star-all", "explicit-none"])
async def test_list_my_tasks_select(select_arg, expected, graph_ctx):
    graph_client, captured = make_capturing_client()

    with graph_ctx(MODULE, graph_client):
        await list_my_tasks(**select_arg)

    assert captured[0].query_parameters.select == expected


async def test_list_my_tasks_filter_narrows_results(graph_ctx):
    # Client-side filter should remove non-matching tasks from the response.
    # Graph Planner ignores $filter on /me/planner/tasks, so filtering is
    # applied in Python after all tasks are fetched via pagination.
    tasks = [make_task("t1", "Project Alpha"), make_task("t2", "Bug Fix")]
    graph_client = MagicMock()
    graph_client.me.planner.tasks.get = AsyncMock(return_value=make_tasks_result(tasks))

    with graph_ctx(MODULE, graph_client):
        result = await list_my_tasks(filter="startswith(title, 'Project')")

    assert len(result) == 1
    assert result[0].title == "Project Alpha"


async def test_list_my_tasks_search_narrows_results(graph_ctx):
    tasks = [make_task("t1", "Project Alpha"), make_task("t2", "Bug Fix")]
    graph_client = MagicMock()
    graph_client.me.planner.tasks.get = AsyncMock(return_value=make_tasks_result(tasks))

    with graph_ctx(MODULE, graph_client):
        result = await list_my_tasks(search="bug")

    assert len(result) == 1
    assert result[0].title == "Bug Fix"


async def test_list_my_tasks_filter_no_match_returns_none(graph_ctx):
    # When all items are filtered out, the tool returns None (same as
    # empty paginated response) so the LLM receives a clear "no results".
    tasks = [make_task("t1", "Alpha")]
    graph_client = MagicMock()
    graph_client.me.planner.tasks.get = AsyncMock(return_value=make_tasks_result(tasks))

    with graph_ctx(MODULE, graph_client):
        result = await list_my_tasks(filter="title eq 'Nonexistent'")

    assert result is None


async def test_list_my_tasks_filter_not_sent_to_graph(graph_ctx):
    # filter/search/count must NOT appear in the RequestConfiguration sent
    # to Graph — they are applied client-side only.
    graph_client, captured = make_capturing_client()

    with graph_ctx(MODULE, graph_client):
        await list_my_tasks(filter="title eq 'x'", search="y")

    config = captured[0]
    assert not hasattr(config.query_parameters, "filter") or config.query_parameters.filter is None
    assert not hasattr(config.query_parameters, "search") or config.query_parameters.search is None
    assert not hasattr(config.query_parameters, "count") or config.query_parameters.count is None


# ---------------------------------------------------------------------------
# get_task_details
# ---------------------------------------------------------------------------


async def test_get_task_details_returns_details(graph_ctx):
    details = make_details()
    graph_client = MagicMock()
    graph_client.planner.tasks.by_planner_task_id.return_value.details.get = AsyncMock(return_value=details)

    with graph_ctx(MODULE, graph_client):
        result = await get_task_details("task-1")

    assert result is details
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
    graph_client = make_patching_graph_client(return_value=updated)

    with graph_ctx(MODULE, graph_client):
        result = await update_task("task-1", '"etag-v1"', title="Updated")

    assert result is updated
    graph_client.planner.tasks.by_planner_task_id.return_value.patch.assert_awaited_once()
    call_kwargs = graph_client.planner.tasks.by_planner_task_id.return_value.patch.call_args
    assert call_kwargs.args[0].title == "Updated"
    # Verify the tool forwards the ETag and requests return=representation.
    config = call_kwargs.kwargs["request_configuration"]
    assert config.headers.get("if-match") == {'"etag-v1"'}
    assert config.headers.get("prefer") == {"return=representation"}


@pytest.mark.parametrize("field,value,attr", [
    ("percentComplete", 50, "percent_complete"),
    ("bucketId", "bucket-abc", "bucket_id"),
    ("assigneePriority", "8585!", "assignee_priority"),
], ids=["percent-complete", "bucket-id", "assignee-priority"])
async def test_update_task_sets_scalar_fields(field, value, attr, graph_ctx):
    graph_client = make_patching_graph_client(return_value=make_task())

    with graph_ctx(MODULE, graph_client):
        await update_task("task-1", '"etag-v1"', **{field: value})

    body = graph_client.planner.tasks.by_planner_task_id.return_value.patch.call_args.args[0]
    assert getattr(body, attr) == value


@pytest.mark.parametrize("due_str,expected_utc", [
    ("2026-05-31T00:00:00", datetime(2026, 5, 31, 0, 0, 0, tzinfo=timezone.utc)),
    ("2026-05-31T10:00:00+05:30", datetime(2026, 5, 31, 4, 30, 0, tzinfo=timezone.utc)),
], ids=["naive-datetime-assumed-utc", "offset-datetime-converted-to-utc"])
async def test_update_task_converts_due_date_to_utc(due_str, expected_utc, graph_ctx):
    graph_client = make_patching_graph_client(return_value=make_task())

    with graph_ctx(MODULE, graph_client):
        await update_task("task-1", '"etag-v1"', dueDateTime=due_str)

    body = graph_client.planner.tasks.by_planner_task_id.return_value.patch.call_args.args[0]
    assert body.due_date_time == expected_utc


async def test_update_task_assign_users_builds_correct_additional_data(graph_ctx):
    graph_client = make_patching_graph_client(return_value=make_task())

    with graph_ctx(MODULE, graph_client):
        await update_task("task-1", '"etag-v1"', assignUserIds=["user-a"], unassignUserIds=["user-b"])

    body = graph_client.planner.tasks.by_planner_task_id.return_value.patch.call_args.args[0]
    assert body.assignments.additional_data["user-a"] == {
        "@odata.type": "#microsoft.graph.plannerAssignment",
        "orderHint": " !",
    }
    assert body.assignments.additional_data["user-b"] is None


async def test_update_task_no_fields_sends_empty_body(graph_ctx):
    """Calling update_task with only required args sends an empty PlannerTask body."""
    graph_client = make_patching_graph_client(return_value=make_task())

    with graph_ctx(MODULE, graph_client):
        await update_task("task-1", '"etag-v1"')

    body = graph_client.planner.tasks.by_planner_task_id.return_value.patch.call_args.args[0]
    assert body.title is None
    assert body.assignments is None


# ---------------------------------------------------------------------------
# update_task — detail fields (description, checklist, references)
# ---------------------------------------------------------------------------
# These tests verify the detail-field path of the consolidated update_task
# tool, which replaced the former update_task_details tool. Detail fields
# are updated via a separate PlannerTaskDetails PATCH within the same call.


async def test_update_task_details_returns_updated_details(graph_ctx):
    """When only detail fields are provided, update_task patches the details
    resource and returns the details result directly."""
    updated = make_details("new desc")
    graph_client = make_patching_graph_client(details_return_value=updated)

    with graph_ctx(MODULE, graph_client):
        result = await update_task("task-1", '"etag-v1"', description="new desc", etagDetails='"details-etag-v1"')

    assert result is updated
    graph_client.planner.tasks.by_planner_task_id.return_value.details.patch.assert_awaited_once()
    body = graph_client.planner.tasks.by_planner_task_id.return_value.details.patch.call_args.args[0]
    assert body.description == "new desc"


async def test_update_task_details_sets_checklist_and_references(graph_ctx):
    graph_client = make_patching_graph_client(details_return_value=make_details())
    checklist = {"guid-1": {"@odata.type": "microsoft.graph.plannerChecklistItem", "title": "Step 1", "isChecked": False}}
    refs = {"https%3A//example%2Ecom": {"@odata.type": "microsoft.graph.plannerExternalReference", "alias": "Ref"}}

    with graph_ctx(MODULE, graph_client):
        await update_task("task-1", '"etag-v1"', checklistItems=checklist, references=refs, etagDetails='"details-etag-v1"')

    body = graph_client.planner.tasks.by_planner_task_id.return_value.details.patch.call_args.args[0]
    assert body.checklist.additional_data == checklist
    assert body.references.additional_data == refs


async def test_update_task_details_auto_injects_odata_type(graph_ctx):
    """Callers should not need to know about @odata.type annotations — the
    tool injects them automatically when missing. Without auto-injection,
    Graph returns 400: 'The given untyped value … is invalid.'
    Docs: https://learn.microsoft.com/en-us/graph/api/plannertaskdetails-update"""
    graph_client = make_patching_graph_client(details_return_value=make_details())
    # Deliberately omit @odata.type from both checklist items and references.
    checklist = {"guid-1": {"title": "Step 1", "isChecked": False}}
    refs = {"https%3A//example%2Ecom": {"alias": "Ref"}}

    with graph_ctx(MODULE, graph_client):
        await update_task("task-1", '"etag-v1"', checklistItems=checklist, references=refs, etagDetails='"details-etag-v1"')

    body = graph_client.planner.tasks.by_planner_task_id.return_value.details.patch.call_args.args[0]
    assert body.checklist.additional_data["guid-1"]["@odata.type"] == "microsoft.graph.plannerChecklistItem"
    assert body.references.additional_data["https%3A//example%2Ecom"]["@odata.type"] == "microsoft.graph.plannerExternalReference"


async def test_update_task_details_preserves_explicit_odata_type(graph_ctx):
    """When callers already provide @odata.type, the tool must not overwrite it."""
    graph_client = make_patching_graph_client(details_return_value=make_details())
    checklist = {"guid-1": {"@odata.type": "#microsoft.graph.plannerChecklistItem", "title": "Step 1", "isChecked": False}}

    with graph_ctx(MODULE, graph_client):
        await update_task("task-1", '"etag-v1"', checklistItems=checklist, etagDetails='"details-etag-v1"')

    body = graph_client.planner.tasks.by_planner_task_id.return_value.details.patch.call_args.args[0]
    # The explicit #-prefixed type must be preserved, not replaced with the un-prefixed default.
    assert body.checklist.additional_data["guid-1"]["@odata.type"] == "#microsoft.graph.plannerChecklistItem"


async def test_update_task_details_null_delete_entries_unaffected(graph_ctx):
    """Null values (used to delete a checklist item or reference) must pass
    through without modification — they are not dicts and have no @odata.type."""
    graph_client = make_patching_graph_client(details_return_value=make_details())
    checklist = {"guid-to-delete": None, "guid-keep": {"title": "Keep", "isChecked": False}}

    with graph_ctx(MODULE, graph_client):
        await update_task("task-1", '"etag-v1"', checklistItems=checklist, etagDetails='"details-etag-v1"')

    body = graph_client.planner.tasks.by_planner_task_id.return_value.details.patch.call_args.args[0]
    assert body.checklist.additional_data["guid-to-delete"] is None
    assert body.checklist.additional_data["guid-keep"]["@odata.type"] == "microsoft.graph.plannerChecklistItem"


async def test_update_task_details_does_not_mutate_caller_dicts(graph_ctx):
    """The tool copies dicts before injecting @odata.type. Without the copy,
    callers that reuse the dict would see unexpected extra keys."""
    graph_client = make_patching_graph_client(details_return_value=make_details())
    checklist = {"guid-1": {"title": "Step 1", "isChecked": False}}
    refs = {"https%3A//example%2Ecom": {"alias": "Ref"}}

    with graph_ctx(MODULE, graph_client):
        await update_task("task-1", '"etag-v1"', checklistItems=checklist, references=refs, etagDetails='"details-etag-v1"')

    # Caller's original dicts must not have @odata.type injected into them.
    assert "@odata.type" not in checklist["guid-1"]
    assert "@odata.type" not in refs["https%3A//example%2Ecom"]


@pytest.mark.parametrize("raw_key,expected_key", [
    ("https://example.com", "https%3A//example%2Ecom"),
    ("http://dev.microsoft.com/path", "http%3A//dev%2Emicrosoft%2Ecom/path"),
    ("https%3A//already%2Eencoded%2Ecom", "https%3A//already%2Eencoded%2Ecom"),
    ("https://example.com?a=1&b=2#section", "https%3A//example%2Ecom?a=1&b=2#section"),
], ids=["raw-url", "raw-url-with-dots", "already-encoded", "url-with-query-and-fragment"])
async def test_update_task_details_encodes_reference_keys(raw_key, expected_key, graph_ctx):
    """Reference keys must have ':' and '.' encoded per Graph Planner convention.
    LLM callers typically send raw URLs; the tool encodes them automatically.
    Already-encoded keys must pass through unchanged (idempotent).
    Docs: https://learn.microsoft.com/en-us/graph/api/plannertaskdetails-update"""
    graph_client = make_patching_graph_client(details_return_value=make_details())
    refs = {raw_key: {"alias": "Test"}}

    with graph_ctx(MODULE, graph_client):
        await update_task("task-1", '"etag-v1"', references=refs, etagDetails='"details-etag-v1"')

    body = graph_client.planner.tasks.by_planner_task_id.return_value.details.patch.call_args.args[0]
    assert expected_key in body.references.additional_data


@pytest.mark.parametrize("preview_type", [
    "automatic", "noPreview", "checklist", "description", "reference",
], ids=["automatic", "noPreview", "checklist", "description", "reference"])
async def test_update_task_details_sets_preview_type(preview_type, graph_ctx):
    """All five documented previewType values must be accepted and forwarded."""
    from msgraph.generated.models.planner_preview_type import PlannerPreviewType

    graph_client = make_patching_graph_client(details_return_value=make_details())

    with graph_ctx(MODULE, graph_client):
        await update_task("task-1", '"etag-v1"', previewType=preview_type, etagDetails='"details-etag-v1"')

    body = graph_client.planner.tasks.by_planner_task_id.return_value.details.patch.call_args.args[0]
    assert body.preview_type == PlannerPreviewType(preview_type)


async def test_update_task_details_invalid_preview_type_raises(graph_ctx):
    """An invalid previewType value must raise ValueError, not silently pass through."""
    graph_client = make_patching_graph_client(details_return_value=make_details())

    with graph_ctx(MODULE, graph_client):
        with pytest.raises(ValueError):
            await update_task("task-1", '"etag-v1"', previewType="invalid", etagDetails='"details-etag-v1"')


@pytest.mark.parametrize("status,code", [
    (403, "MaximumChecklistItemsOnTask"),
    (400, "BadRequest"),
], ids=["403-forbidden", "400-bad-request"])
async def test_update_task_details_odata_error(status, code, graph_ctx, make_odata_error):
    graph_client = make_patching_graph_client(details_side_effect=make_odata_error(status, code))

    with graph_ctx(MODULE, graph_client):
        with pytest.raises(RuntimeError, match="Graph API error"):
            await update_task("task-1", '"etag-v1"', description="x", etagDetails='"details-etag-v1"')


# ---------------------------------------------------------------------------
# update_task — etagDetails auto-refresh
# ---------------------------------------------------------------------------


async def test_update_task_auto_refreshes_details_etag(graph_ctx):
    """When etagDetails is not provided, update_task auto-refreshes the ETag
    from the details resource via GET before patching. Without this fallback,
    callers that only have the task-level ETag would need an extra round-trip.
    Docs: https://learn.microsoft.com/en-us/graph/api/plannertaskdetails-update"""
    updated = make_details("refreshed")
    graph_client = make_patching_graph_client(details_return_value=updated)

    with graph_ctx(MODULE, graph_client):
        result = await update_task("task-1", '"etag-v1"', description="refreshed")

    # The details GET should have been called to fetch the current ETag.
    graph_client.planner.tasks.by_planner_task_id.return_value.details.get.assert_awaited_once()
    # The details PATCH should have used the auto-refreshed ETag.
    config = graph_client.planner.tasks.by_planner_task_id.return_value.details.patch.call_args.kwargs["request_configuration"]
    assert config.headers.get("if-match") == {'"auto-details-etag"'}
    assert result is updated


async def test_update_task_uses_explicit_details_etag(graph_ctx):
    """When etagDetails is provided, update_task uses it directly without
    auto-refreshing. This avoids an extra GET round-trip when the caller
    already has the details ETag from a prior get_task_details call."""
    updated = make_details("explicit")
    graph_client = make_patching_graph_client(details_return_value=updated)

    with graph_ctx(MODULE, graph_client):
        result = await update_task("task-1", '"etag-v1"', description="explicit", etagDetails='"my-details-etag"')

    # The details GET should NOT have been called since etagDetails was provided.
    graph_client.planner.tasks.by_planner_task_id.return_value.details.get.assert_not_awaited()
    # The details PATCH should use the explicitly provided ETag.
    config = graph_client.planner.tasks.by_planner_task_id.return_value.details.patch.call_args.kwargs["request_configuration"]
    assert config.headers.get("if-match") == {'"my-details-etag"'}
    assert result is updated


# ---------------------------------------------------------------------------
# update_task — combined standard + detail fields
# ---------------------------------------------------------------------------


async def test_update_task_combined_returns_both_results(graph_ctx):
    """When both standard and detail fields are provided, update_task patches
    both resources and returns a dict with 'task' and 'details' keys."""
    updated_task = make_task(title="Updated")
    updated_details = make_details("new desc")
    graph_client = make_patching_graph_client(
        return_value=updated_task,
        details_return_value=updated_details,
    )

    with graph_ctx(MODULE, graph_client):
        result = await update_task(
            "task-1", '"etag-v1"',
            title="Updated",
            description="new desc",
            etagDetails='"details-etag-v1"',
        )

    # Both PATCHes should have been called.
    graph_client.planner.tasks.by_planner_task_id.return_value.patch.assert_awaited_once()
    graph_client.planner.tasks.by_planner_task_id.return_value.details.patch.assert_awaited_once()
    # Result should be a dict containing both resources.
    assert isinstance(result, dict)
    assert result["task"] is updated_task
    assert result["details"] is updated_details


# ---------------------------------------------------------------------------
# delete_task
# ---------------------------------------------------------------------------


async def test_delete_task_delegates_to_sdk(graph_ctx):
    graph_client = make_patching_graph_client()

    with graph_ctx(MODULE, graph_client):
        result = await delete_task("task-1", '"etag-v1"')

    delete_mock = graph_client.planner.tasks.by_planner_task_id.return_value.delete
    delete_mock.assert_awaited_once()
    # Verify the ETag reaches the If-Match header on the request configuration.
    config = delete_mock.call_args.kwargs["request_configuration"]
    assert config.headers.get("if-match") == {'"etag-v1"'}
    assert result == "Deleted task 'task-1'."


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

    assert result == tasks
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
    ({}, None),
    ({"select": "*all"}, None),
    ({"select": None}, None),
], ids=["custom-csv", "default", "star-all", "explicit-none"])
async def test_list_tasks_select(select_arg, expected, graph_ctx):
    graph_client, captured = make_plan_capturing_client()

    with graph_ctx(MODULE, graph_client):
        await list_tasks("plan-1", **select_arg)

    assert captured[0].query_parameters.select == expected


async def test_list_tasks_filter_narrows_results(graph_ctx):
    tasks = [make_task("t1", "Project Alpha"), make_task("t2", "Bug Fix")]
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.tasks.get = AsyncMock(
        return_value=make_tasks_result(tasks)
    )

    with graph_ctx(MODULE, graph_client):
        result = await list_tasks("plan-1", filter="title eq 'Bug Fix'")

    assert len(result) == 1
    assert result[0].title == "Bug Fix"


async def test_list_tasks_search_narrows_results(graph_ctx):
    tasks = [make_task("t1", "Project Alpha"), make_task("t2", "Bug Fix")]
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.tasks.get = AsyncMock(
        return_value=make_tasks_result(tasks)
    )

    with graph_ctx(MODULE, graph_client):
        result = await list_tasks("plan-1", search="alpha")

    assert len(result) == 1
    assert result[0].title == "Project Alpha"


async def test_list_tasks_filter_no_match_returns_none(graph_ctx):
    tasks = [make_task("t1", "Alpha")]
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.tasks.get = AsyncMock(
        return_value=make_tasks_result(tasks)
    )

    with graph_ctx(MODULE, graph_client):
        result = await list_tasks("plan-1", filter="title eq 'Nonexistent'")

    assert result is None


async def test_list_tasks_filter_not_sent_to_graph(graph_ctx):
    graph_client, captured = make_plan_capturing_client()

    with graph_ctx(MODULE, graph_client):
        await list_tasks("plan-1", filter="title eq 'x'", search="y")

    config = captured[0]
    assert not hasattr(config.query_parameters, "filter") or config.query_parameters.filter is None
    assert not hasattr(config.query_parameters, "search") or config.query_parameters.search is None
    assert not hasattr(config.query_parameters, "count") or config.query_parameters.count is None


# ---------------------------------------------------------------------------
# create_task
# ---------------------------------------------------------------------------


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


@pytest.mark.parametrize("kwarg,sdk_attr,dt_str,expected_utc", [
    ("dueDateTime",   "due_date_time",   "2026-05-31T00:00:00",       datetime(2026, 5, 31, 0,  0, 0, tzinfo=timezone.utc)),
    ("dueDateTime",   "due_date_time",   "2026-05-31T10:00:00+05:30", datetime(2026, 5, 31, 4, 30, 0, tzinfo=timezone.utc)),
    ("startDateTime", "start_date_time", "2026-05-01T00:00:00",       datetime(2026, 5,  1, 0,  0, 0, tzinfo=timezone.utc)),
    ("startDateTime", "start_date_time", "2026-05-01T06:00:00+06:00", datetime(2026, 5,  1, 0,  0, 0, tzinfo=timezone.utc)),
], ids=["due-naive", "due-offset", "start-naive", "start-offset"])
async def test_create_task_converts_date_to_utc(kwarg, sdk_attr, dt_str, expected_utc, graph_ctx):
    graph_client = MagicMock()
    graph_client.planner.tasks.post = AsyncMock(return_value=make_task())

    with graph_ctx(MODULE, graph_client):
        await create_task("plan-1", "bucket-1", "Task", **{kwarg: dt_str})

    body = graph_client.planner.tasks.post.call_args.args[0]
    assert getattr(body, sdk_attr) == expected_utc


async def test_create_task_raises_when_start_after_due(graph_ctx):
    with graph_ctx(MODULE, MagicMock()):
        with pytest.raises(ValueError, match="must not be after"):
            await create_task(
                "plan-1", "bucket-1", "Task",
                startDateTime="2026-06-01T00:00:00",
                dueDateTime="2026-05-01T00:00:00",
            )


async def test_create_task_assigns_users(graph_ctx):
    graph_client = MagicMock()
    graph_client.planner.tasks.post = AsyncMock(return_value=make_task())

    with graph_ctx(MODULE, graph_client):
        await create_task("plan-1", "bucket-1", "Task", assignUserIds=["user-a", "user-b"])

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
        await create_task("plan-1", "bucket-1", "Task", percentComplete=50)

    body = graph_client.planner.tasks.post.call_args.args[0]
    assert body.percent_complete == 50


@pytest.mark.parametrize("status,code,exc_type", [
    (403, "MaximumTasksInProject", ValueError),
    (400, "BadRequest", RuntimeError),
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
        assert "Graph API error" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Tests: list_task_fields
# ---------------------------------------------------------------------------


async def test_list_task_fields_returns_list():
    result = await list_task_fields()
    assert isinstance(result, list)
    assert len(result) > 0
    for item in result:
        assert "name" in item
        assert "detailed" in item
        assert "type" in item
        assert "description" in item
        assert "writable" in item
        assert isinstance(item["detailed"], bool)
        # writable may be None for unannotated SDK fields; bool when known.
        assert item["writable"] is None or isinstance(item["writable"], bool)


async def test_list_task_fields_no_token_required():
    # Patch auth and assert it is never touched because list_task_fields is a
    # static schema tool. Without assert_not_called(), a regression that starts
    # calling get_access_token() but tolerates None would still pass this test.
    # Docs: https://docs.python.org/3/library/unittest.mock.html#unittest.mock.Mock.assert_not_called
    with patch(f"{MODULE}.get_access_token", return_value=None) as mock_get_access_token:
        result = await list_task_fields()
    mock_get_access_token.assert_not_called()
    assert result is not None


async def test_list_task_fields_has_detailed_and_non_detailed():
    result = await list_task_fields()
    detailed = [f for f in result if f["detailed"]]
    non_detailed = [f for f in result if not f["detailed"]]
    assert len(detailed) > 0, "expected at least one detailed field"
    assert len(non_detailed) > 0, "expected at least one non-detailed field"


@pytest.mark.parametrize("field_name,expected_detailed,expected_writable", [
    ("id", False, False),
    ("title", False, True),
    ("percentComplete", False, True),
    ("completedDateTime", False, False),
    ("description", True, True),
    ("checklist", True, True),
    ("references", True, True),
], ids=["id-not-detailed", "title-not-detailed", "percent-not-detailed", "completedDateTime-readonly", "description-detailed", "checklist-detailed", "references-detailed"])
async def test_list_task_fields_known_field_values(field_name, expected_detailed, expected_writable):
    result = await list_task_fields()
    field = next((f for f in result if f["name"] == field_name), None)
    assert field is not None, f"field {field_name!r} not found in result"
    assert field["detailed"] is expected_detailed
    assert field["writable"] is expected_writable
    assert isinstance(field["type"], str) and field["type"] != ""
    assert isinstance(field["description"], str)

