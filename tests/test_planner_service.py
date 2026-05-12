"""Unit tests for PlannerService — make_config, with_retry, refresh_etag."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_task import PlannerTask

from src.services.planner_service import PlannerService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_task(etag: str = '"etag-v1"') -> PlannerTask:
    task = PlannerTask()
    task.additional_data = {"@odata.etag": etag}
    return task


# ---------------------------------------------------------------------------
# make_config
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefer,expect_prefer", [
    (True, {"return=representation"}),
    (False, set()),
], ids=["prefer-on", "prefer-off"])
def test_make_config_headers(prefer, expect_prefer):
    config = PlannerService(MagicMock()).make_config('"etag-abc"', prefer_representation=prefer)
    assert config.headers.get("if-match") == {'"etag-abc"'}
    assert config.headers.get("prefer") == expect_prefer


def test_configs_do_not_share_headers():
    """Guards against the SDK's shared mutable HeadersCollection default."""
    svc = PlannerService(MagicMock())
    c1 = svc.make_config('"etag-1"')
    c2 = svc.make_config('"etag-2"')
    assert c1.headers is not c2.headers
    assert c1.headers.get("if-match") == {'"etag-1"'}
    assert c2.headers.get("if-match") == {'"etag-2"'}


# ---------------------------------------------------------------------------
# refresh_etag
# ---------------------------------------------------------------------------


async def test_refresh_etag_returns_etag():
    task = make_task('"etag-fresh"')
    etag = await PlannerService.refresh_etag(AsyncMock(return_value=task), "task 'x'")
    assert etag == '"etag-fresh"'


async def test_refresh_etag_raises_when_none():
    with pytest.raises(ValueError, match="No @odata.etag found on task 'x'"):
        await PlannerService.refresh_etag(AsyncMock(return_value=None), "task 'x'")


async def test_refresh_etag_raises_when_missing_key():
    task = PlannerTask()
    task.additional_data = {}
    with pytest.raises(ValueError, match="No @odata.etag found"):
        await PlannerService.refresh_etag(AsyncMock(return_value=task), "task 'x'")


# ---------------------------------------------------------------------------
# with_retry
# ---------------------------------------------------------------------------


async def test_with_retry_success():
    """Operation succeeds on first attempt — no refresh needed."""
    svc = PlannerService(MagicMock())
    op = AsyncMock(return_value="ok")
    refresh = AsyncMock()

    result = await svc.with_retry('"etag-v1"', op, refresh)

    assert result == "ok"
    op.assert_awaited_once_with('"etag-v1"')
    refresh.assert_not_awaited()


@pytest.mark.parametrize("status", [412, 409], ids=["precondition-failed-412", "conflict-409"])
async def test_with_retry_retries_on_conflict(status, make_odata_error):
    """412/409 triggers a single refresh-and-retry."""
    svc = PlannerService(MagicMock())
    op = AsyncMock(side_effect=[make_odata_error(status), "ok"])
    refresh = AsyncMock(return_value='"etag-fresh"')

    result = await svc.with_retry('"etag-stale"', op, refresh)

    assert result == "ok"
    assert op.await_count == 2
    op.assert_awaited_with('"etag-fresh"')
    refresh.assert_awaited_once()


@pytest.mark.parametrize("status,code", [
    (400, "BadRequest"),
    (403, "Forbidden"),
], ids=["bad-request-400", "forbidden-403"])
async def test_with_retry_non_retryable_raises(status, code, make_odata_error):
    """Non-409/412 errors are re-raised as RuntimeError with a clean message."""
    svc = PlannerService(MagicMock())
    op = AsyncMock(side_effect=make_odata_error(status, code))
    refresh = AsyncMock()

    with pytest.raises(RuntimeError, match=f"Graph API error \\({status}\\)"):
        await svc.with_retry('"etag-v1"', op, refresh)

    refresh.assert_not_awaited()


async def test_with_retry_sanitises_retry_failure(make_odata_error):
    """If the retry itself fails, the ODataError is also sanitised."""
    svc = PlannerService(MagicMock())
    op = AsyncMock(side_effect=[make_odata_error(412), make_odata_error(500, "InternalServerError")])
    refresh = AsyncMock(return_value='"etag-fresh"')

    with pytest.raises(RuntimeError, match="Graph API error \\(500\\)"):
        await svc.with_retry('"etag-stale"', op, refresh)


# ---------------------------------------------------------------------------
# clean_graph_error
# ---------------------------------------------------------------------------


def test_clean_graph_error_includes_status_and_message(make_odata_error):
    exc = make_odata_error(404, "ResourceNotFound", "The requested item is not found.")
    result = PlannerService.clean_graph_error(exc)

    assert result == "Graph API error (404): The requested item is not found."


def test_clean_graph_error_handles_missing_error_object():
    exc = ODataError()
    exc.response_status_code = 500
    exc.error = None

    result = PlannerService.clean_graph_error(exc)

    assert result == "Graph API error (500): Unknown error"


# ---------------------------------------------------------------------------
# serialize_graph_object / serialize_graph_list
# ---------------------------------------------------------------------------


def test_serialize_graph_object_returns_dict():
    """Verifies Kiota model → dict round-trip produces correct output."""
    svc = PlannerService(MagicMock(), serialize=True)
    task = PlannerTask()
    task.id = "task-abc"
    task.title = "Ship feature"
    task.percent_complete = 50

    result = svc.serialize_graph_object(task)

    assert isinstance(result, dict)
    assert result["id"] == "task-abc"
    assert result["title"] == "Ship feature"
    assert result["percentComplete"] == 50


def test_serialize_graph_object_returns_raw_when_disabled():
    """When serialize=False the original Kiota object is returned unchanged."""
    svc = PlannerService(MagicMock(), serialize=False)
    task = PlannerTask()
    task.id = "task-abc"

    result = svc.serialize_graph_object(task)

    assert result is task


def test_serialize_graph_list_returns_list_of_dicts():
    svc = PlannerService(MagicMock(), serialize=True)
    t1 = PlannerTask()
    t1.id = "t1"
    t1.title = "First"
    t2 = PlannerTask()
    t2.id = "t2"
    t2.title = "Second"

    result = svc.serialize_graph_list([t1, t2])

    assert len(result) == 2
    assert all(isinstance(r, dict) for r in result)
    assert result[0]["id"] == "t1"
    assert result[1]["title"] == "Second"


def test_serialize_graph_list_returns_raw_when_disabled():
    svc = PlannerService(MagicMock(), serialize=False)
    tasks = [PlannerTask(), PlannerTask()]

    result = svc.serialize_graph_list(tasks)

    assert result is tasks


# ---------------------------------------------------------------------------
# filter_items — client-side OData filtering
# ---------------------------------------------------------------------------


def _make_task(title: str = "My Task", percent_complete: int = 0) -> PlannerTask:
    """Create a PlannerTask with the given title and percent_complete."""
    t = PlannerTask()
    t.title = title
    t.percent_complete = percent_complete
    return t


@pytest.fixture
def sample_tasks() -> list[PlannerTask]:
    return [
        _make_task("Project Alpha", 0),
        _make_task("Project Beta", 50),
        _make_task("Design Review", 100),
        _make_task("Bug Fix", 25),
    ]


# -- eq (string, case-insensitive) --


def test_filter_eq_string(sample_tasks):
    result = PlannerService.filter_items(sample_tasks, filter="title eq 'Project Alpha'")
    assert len(result) == 1
    assert result[0].title == "Project Alpha"


def test_filter_eq_string_case_insensitive(sample_tasks):
    # LLM callers may not match case exactly. Case-insensitive comparison
    # ensures ``title eq 'project alpha'`` still matches.
    result = PlannerService.filter_items(sample_tasks, filter="title eq 'project alpha'")
    assert len(result) == 1
    assert result[0].title == "Project Alpha"


# -- eq (numeric) --


def test_filter_eq_numeric(sample_tasks):
    result = PlannerService.filter_items(sample_tasks, filter="percentComplete eq 50")
    assert len(result) == 1
    assert result[0].title == "Project Beta"


# -- ne --


def test_filter_ne(sample_tasks):
    result = PlannerService.filter_items(sample_tasks, filter="percentComplete ne 0")
    assert len(result) == 3
    assert all(t.percent_complete != 0 for t in result)


# -- eq (boolean: true / false / null) --


def test_filter_eq_true():
    # odata-v4-query parses ``true`` as an identifier, not a literal.
    # Without special handling, ``hasDescription eq true`` would compare
    # the field value against None (getattr for "true") and always fail.
    t1 = _make_task("With desc")
    t1.has_description = True
    t2 = _make_task("No desc")
    t2.has_description = False
    result = PlannerService.filter_items([t1, t2], filter="hasDescription eq true")
    assert len(result) == 1
    assert result[0].title == "With desc"


def test_filter_eq_false():
    t1 = _make_task("With desc")
    t1.has_description = True
    t2 = _make_task("No desc")
    t2.has_description = False
    result = PlannerService.filter_items([t1, t2], filter="hasDescription eq false")
    assert len(result) == 1
    assert result[0].title == "No desc"


def test_filter_eq_null():
    t1 = _make_task("Has title")
    t2 = PlannerTask()
    t2.title = None
    t2.percent_complete = 0
    result = PlannerService.filter_items([t1, t2], filter="title eq null")
    assert len(result) == 1
    assert result[0].title is None


def test_filter_ne_null():
    t1 = _make_task("Has title")
    t2 = PlannerTask()
    t2.title = None
    t2.percent_complete = 0
    result = PlannerService.filter_items([t1, t2], filter="title ne null")
    assert len(result) == 1
    assert result[0].title == "Has title"


# -- gt, ge, lt, le --


@pytest.mark.parametrize("op,value,expected_count", [
    ("gt", 25, 2),
    ("ge", 25, 3),
    ("lt", 50, 2),
    ("le", 50, 3),
], ids=["gt-25", "ge-25", "lt-50", "le-50"])
def test_filter_comparison_operators(op, value, expected_count, sample_tasks):
    result = PlannerService.filter_items(
        sample_tasks, filter=f"percentComplete {op} {value}"
    )
    assert len(result) == expected_count


# -- startswith --


def test_filter_startswith(sample_tasks):
    result = PlannerService.filter_items(sample_tasks, filter="startswith(title, 'Project')")
    assert len(result) == 2
    assert all(t.title.startswith("Project") for t in result)


def test_filter_startswith_case_insensitive(sample_tasks):
    result = PlannerService.filter_items(sample_tasks, filter="startswith(title, 'project')")
    assert len(result) == 2


# -- endswith --


def test_filter_endswith(sample_tasks):
    result = PlannerService.filter_items(sample_tasks, filter="endswith(title, 'Review')")
    assert len(result) == 1
    assert result[0].title == "Design Review"


# -- contains --


def test_filter_contains(sample_tasks):
    result = PlannerService.filter_items(sample_tasks, filter="contains(title, 'ject')")
    assert len(result) == 2


# -- compound: and --


def test_filter_and(sample_tasks):
    result = PlannerService.filter_items(
        sample_tasks, filter="startswith(title, 'Project') and percentComplete eq 0"
    )
    assert len(result) == 1
    assert result[0].title == "Project Alpha"


# -- compound: or --


def test_filter_or(sample_tasks):
    result = PlannerService.filter_items(
        sample_tasks, filter="title eq 'Bug Fix' or title eq 'Design Review'"
    )
    assert len(result) == 2


# -- not --


def test_filter_not(sample_tasks):
    result = PlannerService.filter_items(
        sample_tasks, filter="not startswith(title, 'Project')"
    )
    assert len(result) == 2
    assert all(not t.title.startswith("Project") for t in result)


# -- search --


def test_search_substring_match(sample_tasks):
    result = PlannerService.filter_items(sample_tasks, search="project")
    assert len(result) == 2


def test_search_strips_quotes(sample_tasks):
    # LLM callers often wrap search terms in quotes.
    result = PlannerService.filter_items(sample_tasks, search='"Bug"')
    assert len(result) == 1
    assert result[0].title == "Bug Fix"


def test_search_custom_fields(sample_tasks):
    # Searching a field that doesn't match should return nothing.
    result = PlannerService.filter_items(
        sample_tasks, search="Project", search_fields=["id"]
    )
    assert len(result) == 0


# -- filter + search combined --


def test_filter_and_search_combined(sample_tasks):
    result = PlannerService.filter_items(
        sample_tasks,
        filter="percentComplete eq 0",
        search="alpha",
    )
    assert len(result) == 1
    assert result[0].title == "Project Alpha"


# -- edge cases --


def test_filter_none_returns_all(sample_tasks):
    result = PlannerService.filter_items(sample_tasks)
    assert result is sample_tasks


def test_filter_empty_list():
    result = PlannerService.filter_items([], filter="title eq 'x'")
    assert result == []


def test_filter_no_match_returns_empty(sample_tasks):
    result = PlannerService.filter_items(sample_tasks, filter="title eq 'Nonexistent'")
    assert result == []


def test_filter_none_field_value():
    # When a field is None on the item, comparisons should return False
    # rather than raising an exception.
    task = PlannerTask()
    task.title = None
    task.percent_complete = 0
    result = PlannerService.filter_items([task], filter="startswith(title, 'x')")
    assert result == []


def test_filter_unsupported_function_raises():
    tasks = [_make_task()]
    with pytest.raises(ValueError, match="Unsupported filter function"):
        PlannerService.filter_items(tasks, filter="substring(title, 1)")


def test_filter_unsupported_operator_raises():
    tasks = [_make_task()]
    with pytest.raises(ValueError, match="Unsupported filter operator"):
        PlannerService.filter_items(tasks, filter="title has 'x'")


# ---------------------------------------------------------------------------
# paginate — top parameter
# ---------------------------------------------------------------------------


def _make_page_result(items):
    """Create a single-page mock result with no next link."""
    result = MagicMock()
    result.value = items
    result.odata_next_link = None
    return result


def _make_request_builder(items):
    """Return a mock request builder whose .get() yields a single page of items."""
    rb = MagicMock()
    rb.get = AsyncMock(return_value=_make_page_result(items))
    rb.request_adapter = MagicMock()
    return rb


async def test_paginate_returns_all_items_when_top_is_none():
    tasks = [_make_task(f"Task {i}") for i in range(5)]
    result = await PlannerService.paginate(_make_request_builder(tasks))
    assert result == tasks


async def test_paginate_top_truncates_to_limit():
    # When top < total items, paginate() must stop collecting after top items
    # so we avoid unnecessary Graph API page requests. Without this, an agent
    # calling list_users(top=10) could fetch thousands of directory users.
    tasks = [_make_task(f"Task {i}") for i in range(10)]
    result = await PlannerService.paginate(_make_request_builder(tasks), top=3)
    assert len(result) == 3
    assert result == tasks[:3]


async def test_paginate_top_larger_than_available_returns_all():
    # When top > total items no truncation should occur — return everything.
    tasks = [_make_task(f"Task {i}") for i in range(4)]
    result = await PlannerService.paginate(_make_request_builder(tasks), top=100)
    assert result == tasks


async def test_paginate_top_exact_boundary():
    # top == len(items) — all items are returned, none are dropped.
    tasks = [_make_task(f"Task {i}") for i in range(5)]
    result = await PlannerService.paginate(_make_request_builder(tasks), top=5)
    assert result == tasks


async def test_paginate_top_one_returns_single_item():
    tasks = [_make_task(f"Task {i}") for i in range(5)]
    result = await PlannerService.paginate(_make_request_builder(tasks), top=1)
    assert len(result) == 1
    assert result[0] is tasks[0]


async def test_paginate_returns_empty_for_none_result():
    rb = MagicMock()
    rb.get = AsyncMock(return_value=None)
    rb.request_adapter = MagicMock()
    result = await PlannerService.paginate(rb)
    assert result == []


async def test_with_retry_sends_fresh_etag_in_if_match_header(make_odata_error):
    """Integration-style: verifies the If-Match header carries the refreshed ETag.

    The generic with_retry tests only assert that ``op`` is called with the new
    etag string. This test wires make_config into the operation lambda — the
    same pattern used by the real tool code — and inspects the actual
    RequestConfiguration header to confirm the refreshed ETag reaches the HTTP
    layer. Without this, a bug in make_config could silently drop the header.
    """
    svc = PlannerService(MagicMock())
    captured_configs: list = []

    async def fake_patch(body, *, request_configuration):
        captured_configs.append(request_configuration)
        if len(captured_configs) == 1:
            raise make_odata_error(412)
        return "patched"

    task = make_task('"etag-refreshed"')
    result = await svc.with_retry(
        '"etag-stale"',
        lambda e: fake_patch(None, request_configuration=svc.make_config(e, prefer_representation=True)),
        AsyncMock(return_value='"etag-refreshed"'),
    )

    assert result == "patched"
    assert len(captured_configs) == 2
    # First attempt uses the stale etag
    assert captured_configs[0].headers.get("if-match") == {'"etag-stale"'}
    # Retry attempt uses the refreshed etag
    assert captured_configs[1].headers.get("if-match") == {'"etag-refreshed"'}
