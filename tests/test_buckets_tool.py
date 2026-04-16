"""Unit tests for list_buckets, create_bucket, and delete_bucket tools."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastmcp.exceptions import AuthorizationError
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_bucket import PlannerBucket

from src.tools.buckets import create_bucket, delete_bucket, list_buckets

MODULE = "src.tools.buckets"
# MODULE is the import path patched by graph_ctx / token_capturing_ctx.
# It must be the module where get_access_token and graph_client_manager are
# *used* (i.e. imported into), not where they are defined. Patching the
# definition site would have no effect on the already-imported references.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_bucket(bucket_id: str = "bucket-1", name: str = "To Do") -> PlannerBucket:
    bucket = PlannerBucket()
    bucket.id = bucket_id
    bucket.name = name
    return bucket


def make_buckets_result(buckets) -> MagicMock:
    result = MagicMock()
    result.value = buckets
    result.odata_next_link = None
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("coro_fn", [
    lambda: list_buckets("plan-1"),
    lambda: create_bucket("plan-1", "My Bucket"),
    lambda: delete_bucket("bucket-1", '"etag-v1"'),
], ids=["list-buckets", "create-bucket", "delete-bucket"])
async def test_no_token_raises(coro_fn):
    with patch(f"{MODULE}.get_access_token", return_value=None):
        with pytest.raises(AuthorizationError):
            await coro_fn()


async def test_list_buckets_returns_buckets(graph_ctx):
    buckets = [make_bucket("b1", "To Do"), make_bucket("b2", "In Progress")]
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.buckets.get = AsyncMock(
        return_value=make_buckets_result(buckets)
    )

    with graph_ctx(MODULE, graph_client):
        result = await list_buckets("plan-1")

    assert result == buckets
    graph_client.planner.plans.by_planner_plan_id.assert_called_once_with("plan-1")


@pytest.mark.parametrize("get_return", [None, make_buckets_result(None)], ids=["result-none", "value-none"])
async def test_list_buckets_returns_none_when_empty(get_return, graph_ctx):
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.buckets.get = AsyncMock(return_value=get_return)

    with graph_ctx(MODULE, graph_client):
        result = await list_buckets("plan-1")

    assert result is None


async def test_list_buckets_forwards_obo_token(token_capturing_ctx):
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.buckets.get = AsyncMock(
        return_value=make_buckets_result([])
    )

    with token_capturing_ctx(MODULE, graph_client, "my-obo") as received:
        await list_buckets("plan-1")

    assert received == ["my-obo"]


async def test_list_buckets_passes_plan_id(graph_ctx):
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.buckets.get = AsyncMock(
        return_value=make_buckets_result([])
    )

    with graph_ctx(MODULE, graph_client):
        await list_buckets("plan-xyz")

    graph_client.planner.plans.by_planner_plan_id.assert_called_once_with("plan-xyz")


# ---------------------------------------------------------------------------
# Tests: create_bucket
# ---------------------------------------------------------------------------


async def test_create_bucket_returns_bucket(graph_ctx):
    bucket = make_bucket("new-bucket-1", "In Progress")
    graph_client = MagicMock()
    graph_client.planner.buckets.post = AsyncMock(return_value=bucket)

    with graph_ctx(MODULE, graph_client):
        result = await create_bucket("plan-1", "In Progress")

    assert result is bucket


async def test_create_bucket_posts_correct_body(graph_ctx):
    captured: list = []

    async def capturing_post(body):
        captured.append(body)
        return make_bucket()

    graph_client = MagicMock()
    graph_client.planner.buckets.post = capturing_post

    with graph_ctx(MODULE, graph_client):
        await create_bucket("plan-xyz", "Backlog")

    assert len(captured) == 1
    assert isinstance(captured[0], PlannerBucket)
    assert captured[0].plan_id == "plan-xyz"
    assert captured[0].name == "Backlog"
    # orderHint " !" is the Graph API sentinel for "place first"; without it
    # Graph rejects the POST with 400 Bad Request.
    assert captured[0].order_hint == " !"


@pytest.mark.parametrize("status,code,exc_type", [
    (403, "AuthorizationRequestDenied", ValueError),
    (400, "BadRequest", ODataError),
], ids=["403-value-error", "400-reraises"])
async def test_create_bucket_odata_error(status, code, exc_type, graph_ctx, make_odata_error):
    graph_client = MagicMock()
    graph_client.planner.buckets.post = AsyncMock(side_effect=make_odata_error(status, code))

    with graph_ctx(MODULE, graph_client):
        with pytest.raises(exc_type) as exc_info:
            await create_bucket("plan-1", "My Bucket")

    if exc_type is ValueError:
        assert "Cannot create bucket" in str(exc_info.value)
    else:
        assert exc_info.value.response_status_code == status


async def test_create_bucket_forwards_obo_token(token_capturing_ctx):
    graph_client = MagicMock()
    graph_client.planner.buckets.post = AsyncMock(return_value=make_bucket())

    with token_capturing_ctx(MODULE, graph_client, "my-obo") as received:
        await create_bucket("plan-1", "My Bucket")

    assert received == ["my-obo"]


# ---------------------------------------------------------------------------
# Tests: delete_bucket
# ---------------------------------------------------------------------------


async def test_delete_bucket_calls_sdk(graph_ctx):
    graph_client = MagicMock()
    graph_client.planner.buckets.by_planner_bucket_id.return_value.delete = AsyncMock()

    with graph_ctx(MODULE, graph_client):
        await delete_bucket("bucket-1", '"etag-v1"')

    graph_client.planner.buckets.by_planner_bucket_id.assert_called_once_with("bucket-1")
    graph_client.planner.buckets.by_planner_bucket_id.return_value.delete.assert_awaited_once()


async def test_delete_bucket_forwards_obo_token(token_capturing_ctx):
    graph_client = MagicMock()
    graph_client.planner.buckets.by_planner_bucket_id.return_value.delete = AsyncMock()

    with token_capturing_ctx(MODULE, graph_client, "my-obo") as received:
        await delete_bucket("bucket-1", '"etag-v1"')

    assert received == ["my-obo"]
