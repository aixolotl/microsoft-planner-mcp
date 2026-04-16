"""Unit tests for PlannerService — _make_config, _with_retry, _refresh_etag."""

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
# _refresh_etag
# ---------------------------------------------------------------------------


async def test_refresh_etag_returns_etag():
    task = make_task('"etag-fresh"')
    etag = await PlannerService._refresh_etag(AsyncMock(return_value=task)(), "task 'x'")
    assert etag == '"etag-fresh"'


async def test_refresh_etag_raises_when_none():
    with pytest.raises(ValueError, match="No @odata.etag found on task 'x'"):
        await PlannerService._refresh_etag(AsyncMock(return_value=None)(), "task 'x'")


async def test_refresh_etag_raises_when_missing_key():
    task = PlannerTask()
    task.additional_data = {}
    with pytest.raises(ValueError, match="No @odata.etag found"):
        await PlannerService._refresh_etag(AsyncMock(return_value=task)(), "task 'x'")


# ---------------------------------------------------------------------------
# _with_retry
# ---------------------------------------------------------------------------


async def test_with_retry_success():
    """Operation succeeds on first attempt — no refresh needed."""
    svc = PlannerService(MagicMock())
    op = AsyncMock(return_value="ok")
    refresh = AsyncMock()

    result = await svc._with_retry('"etag-v1"', op, refresh)

    assert result == "ok"
    op.assert_awaited_once_with('"etag-v1"')
    refresh.assert_not_awaited()


@pytest.mark.parametrize("status", [412, 409], ids=["precondition-failed-412", "conflict-409"])
async def test_with_retry_retries_on_conflict(status, make_odata_error):
    """412/409 triggers a single refresh-and-retry."""
    svc = PlannerService(MagicMock())
    op = AsyncMock(side_effect=[make_odata_error(status), "ok"])
    refresh = AsyncMock(return_value='"etag-fresh"')

    result = await svc._with_retry('"etag-stale"', op, refresh)

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
        await svc._with_retry('"etag-v1"', op, refresh)

    assert exc_info.value.response_status_code == status
    refresh.assert_not_awaited()
