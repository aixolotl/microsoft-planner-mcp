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
