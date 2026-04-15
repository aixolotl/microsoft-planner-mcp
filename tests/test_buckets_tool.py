"""Unit tests for list_buckets tool."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastmcp.exceptions import AuthorizationError
from msgraph.generated.models.planner_bucket import PlannerBucket

from src.tools.buckets import list_buckets


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_access_token(token_str: str = "test-obo-token") -> MagicMock:
    token = MagicMock()
    token.token = token_str
    return token


def make_bucket(bucket_id: str = "bucket-1", name: str = "To Do") -> PlannerBucket:
    bucket = PlannerBucket()
    bucket.id = bucket_id
    bucket.name = name
    return bucket


def make_buckets_result(buckets: list[PlannerBucket] | None) -> MagicMock:
    result = MagicMock()
    result.value = buckets
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_buckets_no_token_raises():
    with patch("src.tools.buckets.get_access_token", return_value=None):
        with pytest.raises(AuthorizationError):
            await list_buckets("plan-1")


@pytest.mark.asyncio
async def test_list_buckets_returns_buckets():
    buckets = [make_bucket("b1", "To Do"), make_bucket("b2", "In Progress")]
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.buckets.get = AsyncMock(
        return_value=make_buckets_result(buckets)
    )

    @asynccontextmanager
    async def _for_user(token):
        yield graph_client

    with patch("src.tools.buckets.get_access_token", return_value=make_access_token()), \
         patch("src.tools.buckets.graph_client_manager") as mock_mgr:
        mock_mgr.for_user = _for_user
        result = await list_buckets("plan-1")

    assert result == buckets
    graph_client.planner.plans.by_planner_plan_id.assert_called_once_with("plan-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("get_return", [None, make_buckets_result(None)], ids=["result-none", "value-none"])
async def test_list_buckets_returns_none_when_empty(get_return):
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.buckets.get = AsyncMock(return_value=get_return)

    @asynccontextmanager
    async def _for_user(token):
        yield graph_client

    with patch("src.tools.buckets.get_access_token", return_value=make_access_token()), \
         patch("src.tools.buckets.graph_client_manager") as mock_mgr:
        mock_mgr.for_user = _for_user
        result = await list_buckets("plan-1")

    assert result is None


@pytest.mark.asyncio
async def test_list_buckets_forwards_obo_token():
    received: list[str] = []

    @asynccontextmanager
    async def _for_user(token: str):
        received.append(token)
        graph_client = MagicMock()
        graph_client.planner.plans.by_planner_plan_id.return_value.buckets.get = AsyncMock(
            return_value=make_buckets_result([])
        )
        yield graph_client

    with patch("src.tools.buckets.get_access_token", return_value=make_access_token("my-obo")), \
         patch("src.tools.buckets.graph_client_manager") as mock_mgr:
        mock_mgr.for_user = _for_user
        await list_buckets("plan-1")

    assert received == ["my-obo"]


@pytest.mark.asyncio
async def test_list_buckets_passes_plan_id():
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.buckets.get = AsyncMock(
        return_value=make_buckets_result([])
    )

    @asynccontextmanager
    async def _for_user(token):
        yield graph_client

    with patch("src.tools.buckets.get_access_token", return_value=make_access_token()), \
         patch("src.tools.buckets.graph_client_manager") as mock_mgr:
        mock_mgr.for_user = _for_user
        await list_buckets("plan-xyz")

    graph_client.planner.plans.by_planner_plan_id.assert_called_once_with("plan-xyz")
