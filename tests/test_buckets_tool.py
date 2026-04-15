"""Unit tests for list_buckets tool."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastmcp.exceptions import AuthorizationError
from msgraph.generated.models.planner_bucket import PlannerBucket

from src.tools.buckets import list_buckets

MODULE = "src.tools.buckets"


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
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_buckets_no_token_raises():
    with patch(f"{MODULE}.get_access_token", return_value=None):
        with pytest.raises(AuthorizationError):
            await list_buckets("plan-1")


@pytest.mark.asyncio
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


@pytest.mark.asyncio
@pytest.mark.parametrize("get_return", [None, make_buckets_result(None)], ids=["result-none", "value-none"])
async def test_list_buckets_returns_none_when_empty(get_return, graph_ctx):
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.buckets.get = AsyncMock(return_value=get_return)

    with graph_ctx(MODULE, graph_client):
        result = await list_buckets("plan-1")

    assert result is None


@pytest.mark.asyncio
async def test_list_buckets_forwards_obo_token(make_access_token):
    received: list[str] = []

    @asynccontextmanager
    async def _for_user(token: str):
        received.append(token)
        graph_client = MagicMock()
        graph_client.planner.plans.by_planner_plan_id.return_value.buckets.get = AsyncMock(
            return_value=make_buckets_result([])
        )
        yield graph_client

    with patch(f"{MODULE}.get_access_token", return_value=make_access_token("my-obo")), \
         patch(f"{MODULE}.graph_client_manager") as mock_mgr:
        mock_mgr.for_user = _for_user
        await list_buckets("plan-1")

    assert received == ["my-obo"]


@pytest.mark.asyncio
async def test_list_buckets_passes_plan_id(graph_ctx):
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.buckets.get = AsyncMock(
        return_value=make_buckets_result([])
    )

    with graph_ctx(MODULE, graph_client):
        await list_buckets("plan-xyz")

    graph_client.planner.plans.by_planner_plan_id.assert_called_once_with("plan-xyz")
