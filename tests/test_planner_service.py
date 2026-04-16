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
    """Non-409/412 errors are re-raised immediately."""
    svc = PlannerService(MagicMock())
    op = AsyncMock(side_effect=make_odata_error(status, code))
    refresh = AsyncMock()

    with pytest.raises(ODataError) as exc_info:
        await svc.with_retry('"etag-v1"', op, refresh)

    assert exc_info.value.response_status_code == status
    refresh.assert_not_awaited()


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
