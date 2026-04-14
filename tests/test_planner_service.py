"""Unit tests for PlannerService — retry logic and ODataError handling."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from msgraph.generated.models.o_data_errors.main_error import MainError
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_task import PlannerTask

from src.services.planner_service import PlannerService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_odata_error(
    status: int,
    code: str = "SomeCode",
    message: str = "Some message",
) -> ODataError:
    err = ODataError()
    err.response_status_code = status
    err.error = MainError(code=code, message=message)
    return err


def make_task(etag: str = '"etag-v1"') -> PlannerTask:
    task = PlannerTask()
    task.additional_data = {"@odata.etag": etag}
    return task


def make_graph_client(
    *,
    patch_return: PlannerTask | None = None,
    patch_side_effect: Exception | list | None = None,
    delete_side_effect: Exception | list | None = None,
    get_task: PlannerTask | None = None,
) -> MagicMock:
    client = MagicMock()

    item_builder = MagicMock()
    item_builder.patch = AsyncMock(return_value=patch_return, side_effect=patch_side_effect)
    item_builder.delete = AsyncMock(side_effect=delete_side_effect)
    item_builder.get = AsyncMock(return_value=get_task)

    client.planner.tasks.by_planner_task_id.return_value = item_builder
    return client


# ---------------------------------------------------------------------------
# _make_config
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefer,expect_prefer", [
    (True, {"return=representation"}),
    (False, set()),
], ids=["prefer-on", "prefer-off"])
def test_make_config_headers(prefer, expect_prefer):
    config = PlannerService(MagicMock())._make_config('"etag-abc"', prefer_representation=prefer)
    assert config.headers.get("if-match") == {'"etag-abc"'}
    assert config.headers.get("prefer") == expect_prefer


def test_configs_do_not_share_headers():
    """Guards against the SDK's shared mutable HeadersCollection default."""
    svc = PlannerService(MagicMock())
    c1 = svc._make_config('"etag-1"')
    c2 = svc._make_config('"etag-2"')
    assert c1.headers is not c2.headers
    assert c1.headers.get("if-match") == {'"etag-1"'}
    assert c2.headers.get("if-match") == {'"etag-2"'}


# ---------------------------------------------------------------------------
# patch_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_task_success():
    updated = make_task('"etag-v2"')
    client = make_graph_client(patch_return=updated)

    result = await PlannerService(client).patch_task("task-1", PlannerTask(), '"etag-v1"')

    assert result is updated
    client.planner.tasks.by_planner_task_id.assert_called_once_with("task-1")
    client.planner.tasks.by_planner_task_id.return_value.patch.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [412, 409])
async def test_patch_task_retries_on_conflict(status):
    fresh_task = make_task('"etag-fresh"')
    updated_task = make_task('"etag-v3"')
    client = make_graph_client(
        patch_side_effect=[make_odata_error(status), updated_task],
        get_task=fresh_task,
    )

    result = await PlannerService(client).patch_task("task-1", PlannerTask(), '"etag-stale"')

    assert result is updated_task
    client.planner.tasks.by_planner_task_id.return_value.get.assert_awaited_once()
    assert client.planner.tasks.by_planner_task_id.return_value.patch.await_count == 2


@pytest.mark.asyncio
async def test_patch_task_retry_uses_fresh_etag():
    """Retry must send the refreshed ETag, not the original stale one."""
    captured: list = []

    async def capturing_patch(body, request_configuration=None):
        captured.append(request_configuration)
        if len(captured) == 1:
            raise make_odata_error(412)
        return make_task('"etag-v3"')

    client = MagicMock()
    item = MagicMock()
    item.patch = capturing_patch
    item.get = AsyncMock(return_value=make_task('"etag-fresh"'))
    client.planner.tasks.by_planner_task_id.return_value = item

    await PlannerService(client).patch_task("task-1", PlannerTask(), '"etag-stale"')

    assert list(captured[0].headers.get("if-match"))[0] == '"etag-stale"'
    assert list(captured[1].headers.get("if-match"))[0] == '"etag-fresh"'


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(400, "BadRequest"), (403, "MaximumTasksInProject")])
async def test_patch_task_non_retryable_raises(status, code):
    client = make_graph_client(patch_side_effect=make_odata_error(status, code))

    with pytest.raises(ODataError) as exc_info:
        await PlannerService(client).patch_task("task-1", PlannerTask(), '"etag-v1"')

    assert exc_info.value.response_status_code == status
    assert exc_info.value.error is not None
    assert exc_info.value.error.code == code


# ---------------------------------------------------------------------------
# delete_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_task_success():
    client = make_graph_client()

    await PlannerService(client).delete_task("task-1", '"etag-v1"')

    client.planner.tasks.by_planner_task_id.return_value.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_task_retries_on_412():
    """Confirms delete_task is wired through _with_retry; 412/409 branch proven by patch tests."""
    client = make_graph_client(
        delete_side_effect=[make_odata_error(412), None],
        get_task=make_task('"etag-fresh"'),
    )

    await PlannerService(client).delete_task("task-1", '"etag-stale"')

    client.planner.tasks.by_planner_task_id.return_value.get.assert_awaited_once()
    assert client.planner.tasks.by_planner_task_id.return_value.delete.await_count == 2


@pytest.mark.asyncio
async def test_delete_task_non_retryable_raises():
    client = make_graph_client(delete_side_effect=make_odata_error(403, "MaximumTasksInProject", "Over limit"))

    with pytest.raises(ODataError) as exc_info:
        await PlannerService(client).delete_task("task-1", '"etag-v1"')

    assert exc_info.value.response_status_code == 403
    assert exc_info.value.error is not None
    assert exc_info.value.error.code == "MaximumTasksInProject"
