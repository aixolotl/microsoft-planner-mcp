"""Unit tests for list_group_plans tool."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastmcp.exceptions import AuthorizationError
from msgraph.generated.models.o_data_errors.main_error import MainError
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_plan import PlannerPlan

from src.tools.group_plans import list_group_plans


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_access_token(token_str: str = "test-obo-token") -> MagicMock:
    token = MagicMock()
    token.token = token_str
    return token


def make_plan(plan_id: str = "plan-1", title: str = "Group Plan") -> PlannerPlan:
    plan = PlannerPlan()
    plan.id = plan_id
    plan.title = title
    return plan


def make_plans_result(plans: list[PlannerPlan] | None) -> MagicMock:
    result = MagicMock()
    result.value = plans
    return result


def make_odata_error(status: int, code: str = "SomeCode", message: str = "msg") -> ODataError:
    err = ODataError()
    err.response_status_code = status
    err.error = MainError(code=code, message=message)
    return err


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_group_plans_no_token_raises():
    with patch("src.tools.group_plans.get_access_token", return_value=None):
        with pytest.raises(AuthorizationError):
            await list_group_plans("group-1")


@pytest.mark.asyncio
async def test_list_group_plans_returns_plans():
    plans = [make_plan("p1"), make_plan("p2")]
    graph_client = MagicMock()
    graph_client.groups.by_group_id.return_value.planner.plans.get = AsyncMock(
        return_value=make_plans_result(plans)
    )

    @asynccontextmanager
    async def _for_user(token):
        yield graph_client

    with patch("src.tools.group_plans.get_access_token", return_value=make_access_token()), \
         patch("src.tools.group_plans.graph_client_manager") as mock_mgr:
        mock_mgr.for_user = _for_user
        result = await list_group_plans("group-1")

    assert result == plans
    graph_client.groups.by_group_id.assert_called_once_with("group-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("get_return", [None, make_plans_result(None)], ids=["result-none", "value-none"])
async def test_list_group_plans_returns_none_when_empty(get_return):
    graph_client = MagicMock()
    graph_client.groups.by_group_id.return_value.planner.plans.get = AsyncMock(return_value=get_return)

    @asynccontextmanager
    async def _for_user(token):
        yield graph_client

    with patch("src.tools.group_plans.get_access_token", return_value=make_access_token()), \
         patch("src.tools.group_plans.graph_client_manager") as mock_mgr:
        mock_mgr.for_user = _for_user
        result = await list_group_plans("group-1")

    assert result is None


@pytest.mark.asyncio
async def test_list_group_plans_403_raises_value_error():
    graph_client = MagicMock()
    graph_client.groups.by_group_id.return_value.planner.plans.get = AsyncMock(
        side_effect=make_odata_error(403, "AuthorizationRequestDenied", "Access denied")
    )

    @asynccontextmanager
    async def _for_user(token):
        yield graph_client

    with patch("src.tools.group_plans.get_access_token", return_value=make_access_token()), \
         patch("src.tools.group_plans.graph_client_manager") as mock_mgr:
        mock_mgr.for_user = _for_user
        with pytest.raises(ValueError, match="Access denied for group 'group-1'"):
            await list_group_plans("group-1")


@pytest.mark.asyncio
async def test_list_group_plans_403_error_message_includes_code():
    graph_client = MagicMock()
    graph_client.groups.by_group_id.return_value.planner.plans.get = AsyncMock(
        side_effect=make_odata_error(403, "AuthorizationRequestDenied", "Insufficient privileges")
    )

    @asynccontextmanager
    async def _for_user(token):
        yield graph_client

    with patch("src.tools.group_plans.get_access_token", return_value=make_access_token()), \
         patch("src.tools.group_plans.graph_client_manager") as mock_mgr:
        mock_mgr.for_user = _for_user
        with pytest.raises(ValueError, match="AuthorizationRequestDenied"):
            await list_group_plans("group-1")


@pytest.mark.asyncio
async def test_list_group_plans_non_403_odata_error_reraises():
    graph_client = MagicMock()
    graph_client.groups.by_group_id.return_value.planner.plans.get = AsyncMock(
        side_effect=make_odata_error(404, "ResourceNotFound")
    )

    @asynccontextmanager
    async def _for_user(token):
        yield graph_client

    with patch("src.tools.group_plans.get_access_token", return_value=make_access_token()), \
         patch("src.tools.group_plans.graph_client_manager") as mock_mgr:
        mock_mgr.for_user = _for_user
        with pytest.raises(ODataError) as exc_info:
            await list_group_plans("group-1")

    assert exc_info.value.response_status_code == 404


@pytest.mark.asyncio
async def test_list_group_plans_forwards_obo_token():
    received: list[str] = []

    @asynccontextmanager
    async def _for_user(token: str):
        received.append(token)
        graph_client = MagicMock()
        graph_client.groups.by_group_id.return_value.planner.plans.get = AsyncMock(
            return_value=make_plans_result([])
        )
        yield graph_client

    with patch("src.tools.group_plans.get_access_token", return_value=make_access_token("my-obo")), \
         patch("src.tools.group_plans.graph_client_manager") as mock_mgr:
        mock_mgr.for_user = _for_user
        await list_group_plans("group-1")

    assert received == ["my-obo"]
