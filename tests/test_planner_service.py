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
    """Build an ODataError with the given HTTP status and error body."""
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
    """Return a deeply-stubbed GraphServiceClient."""
    client = MagicMock()

    patch_mock = AsyncMock(return_value=patch_return, side_effect=patch_side_effect)
    delete_mock = AsyncMock(side_effect=delete_side_effect)
    get_mock = AsyncMock(return_value=get_task)

    item_builder = MagicMock()
    item_builder.patch = patch_mock
    item_builder.delete = delete_mock
    item_builder.get = get_mock

    client.planner.tasks.by_planner_task_id.return_value = item_builder
    return client


# ---------------------------------------------------------------------------
# _patch_config / _delete_config (static methods)
# ---------------------------------------------------------------------------


def test_patch_config_headers():
    svc = PlannerService(MagicMock())
    config = svc._patch_config('"etag-abc"')
    assert config.headers.get("if-match") == {'"etag-abc"'}
    assert config.headers.get("prefer") == {"return=representation"}


def test_delete_config_headers():
    svc = PlannerService(MagicMock())
    config = svc._delete_config('"etag-xyz"')
    assert config.headers.get("if-match") == {'"etag-xyz"'}
    # DELETE does not set Prefer header
    assert config.headers.get("prefer") == set()


def test_configs_do_not_share_headers():
    svc = PlannerService(MagicMock())
    c1 = svc._patch_config('"etag-1"')
    c2 = svc._patch_config('"etag-2"')
    assert c1.headers is not c2.headers
    assert c1.headers.get("if-match") == {'"etag-1"'}
    assert c2.headers.get("if-match") == {'"etag-2"'}


# ---------------------------------------------------------------------------
# patch_task — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_task_success():
    updated = make_task('"etag-v2"')
    client = make_graph_client(patch_return=updated)
    svc = PlannerService(client)

    result = await svc.patch_task("task-1", PlannerTask(), '"etag-v1"')

    assert result is updated
    client.planner.tasks.by_planner_task_id.assert_called_once_with("task-1")
    client.planner.tasks.by_planner_task_id.return_value.patch.assert_awaited_once()


# ---------------------------------------------------------------------------
# patch_task — 412 triggers retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_task_retries_on_412():
    fresh_task = make_task('"etag-fresh"')
    updated_task = make_task('"etag-v3"')

    # First patch raises 412; retry succeeds
    client = make_graph_client(
        patch_side_effect=[make_odata_error(412), updated_task],
        get_task=fresh_task,
    )
    svc = PlannerService(client)

    result = await svc.patch_task("task-1", PlannerTask(), '"etag-stale"')

    assert result is updated_task
    # GET was called to refresh ETag
    client.planner.tasks.by_planner_task_id.return_value.get.assert_awaited_once()
    assert client.planner.tasks.by_planner_task_id.return_value.patch.await_count == 2


@pytest.mark.asyncio
async def test_patch_task_retries_on_409():
    fresh_task = make_task('"etag-fresh"')
    updated_task = make_task('"etag-v3"')

    client = make_graph_client(
        patch_side_effect=[make_odata_error(409), updated_task],
        get_task=fresh_task,
    )
    svc = PlannerService(client)

    result = await svc.patch_task("task-1", PlannerTask(), '"etag-stale"')

    assert result is updated_task
    client.planner.tasks.by_planner_task_id.return_value.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_patch_task_retry_uses_fresh_etag():
    """The retry call must use the refreshed ETag, not the original."""
    fresh_task = make_task('"etag-fresh"')

    captured_configs: list = []

    async def capturing_patch(body, request_configuration=None):
        captured_configs.append(request_configuration)
        if len(captured_configs) == 1:
            raise make_odata_error(412)
        return make_task('"etag-v3"')

    client = MagicMock()
    item_builder = MagicMock()
    item_builder.patch = capturing_patch
    item_builder.get = AsyncMock(return_value=fresh_task)
    client.planner.tasks.by_planner_task_id.return_value = item_builder

    svc = PlannerService(client)
    await svc.patch_task("task-1", PlannerTask(), '"etag-stale"')

    first_etag = list(captured_configs[0].headers.get("if-match"))[0]
    retry_etag = list(captured_configs[1].headers.get("if-match"))[0]
    assert first_etag == '"etag-stale"'
    assert retry_etag == '"etag-fresh"'


# ---------------------------------------------------------------------------
# patch_task — non-retryable errors re-raised as ODataError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_task_400_raises_odata_error():
    client = make_graph_client(patch_side_effect=make_odata_error(400, "BadRequest", "Invalid field"))
    svc = PlannerService(client)

    with pytest.raises(ODataError) as exc_info:
        await svc.patch_task("task-1", PlannerTask(), '"etag-v1"')

    assert exc_info.value.response_status_code == 400


@pytest.mark.asyncio
async def test_patch_task_403_raises_odata_error():
    client = make_graph_client(patch_side_effect=make_odata_error(403, "MaximumTasksInProject", "Limit hit"))
    svc = PlannerService(client)

    with pytest.raises(ODataError) as exc_info:
        await svc.patch_task("task-1", PlannerTask(), '"etag-v1"')

    assert exc_info.value.response_status_code == 403
    assert exc_info.value.error.code == "MaximumTasksInProject"


# ---------------------------------------------------------------------------
# delete_task — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_task_success():
    client = make_graph_client()
    svc = PlannerService(client)

    await svc.delete_task("task-1", '"etag-v1"')

    client.planner.tasks.by_planner_task_id.assert_called_with("task-1")
    client.planner.tasks.by_planner_task_id.return_value.delete.assert_awaited_once()


# ---------------------------------------------------------------------------
# delete_task — 412/409 triggers retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_task_retries_on_412():
    fresh_task = make_task('"etag-fresh"')
    client = make_graph_client(
        delete_side_effect=[make_odata_error(412), None],
        get_task=fresh_task,
    )
    svc = PlannerService(client)

    await svc.delete_task("task-1", '"etag-stale"')

    client.planner.tasks.by_planner_task_id.return_value.get.assert_awaited_once()
    assert client.planner.tasks.by_planner_task_id.return_value.delete.await_count == 2


@pytest.mark.asyncio
async def test_delete_task_retries_on_409():
    fresh_task = make_task('"etag-fresh"')
    client = make_graph_client(
        delete_side_effect=[make_odata_error(409), None],
        get_task=fresh_task,
    )
    svc = PlannerService(client)

    await svc.delete_task("task-1", '"etag-stale"')

    client.planner.tasks.by_planner_task_id.return_value.get.assert_awaited_once()


# ---------------------------------------------------------------------------
# delete_task — non-retryable errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_task_404_raises_odata_error():
    client = make_graph_client(delete_side_effect=make_odata_error(404, "NotFound", "Task not found"))
    svc = PlannerService(client)

    with pytest.raises(ODataError) as exc_info:
        await svc.delete_task("task-1", '"etag-v1"')

    assert exc_info.value.response_status_code == 404


@pytest.mark.asyncio
async def test_delete_task_403_raises_odata_error():
    client = make_graph_client(delete_side_effect=make_odata_error(403, "MaximumTasksInProject", "Over limit"))
    svc = PlannerService(client)

    with pytest.raises(ODataError) as exc_info:
        await svc.delete_task("task-1", '"etag-v1"')

    assert exc_info.value.response_status_code == 403
    assert exc_info.value.error.code == "MaximumTasksInProject"
