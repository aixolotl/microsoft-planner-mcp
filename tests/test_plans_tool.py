"""Unit tests for the list_my_plans tool."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastmcp.exceptions import AuthorizationError
from msgraph.generated.models.planner_plan import PlannerPlan

from src.tools.plans import list_my_plans


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_access_token(token_str: str = "test-obo-token") -> MagicMock:
    token = MagicMock()
    token.token = token_str
    return token


def make_plan(plan_id: str = "plan-1", title: str = "My Plan") -> PlannerPlan:
    plan = PlannerPlan()
    plan.id = plan_id
    plan.title = title
    return plan


def make_plans_result(plans: list[PlannerPlan] | None) -> MagicMock:
    result = MagicMock()
    result.value = plans
    return result


def make_graph_client(plans: list[PlannerPlan] | None = None) -> MagicMock:
    client = MagicMock()
    client.me.planner.plans.get = AsyncMock(return_value=make_plans_result(plans))
    return client


def make_capturing_client() -> tuple[MagicMock, list]:
    """Returns (graph_client, captured_configs) where .get stores RequestConfigurations."""
    captured: list = []

    async def capturing_get(request_configuration=None):
        captured.append(request_configuration)
        return make_plans_result([])

    client = MagicMock()
    client.me.planner.plans.get = capturing_get
    return client, captured


# ---------------------------------------------------------------------------
# Tests: authorisation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_token_raises_authorization_error():
    with patch("src.tools.plans.get_access_token", return_value=None):
        with pytest.raises(AuthorizationError):
            await list_my_plans()


# ---------------------------------------------------------------------------
# Tests: return values
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_plan_list():
    plans = [make_plan("plan-1"), make_plan("plan-2")]
    graph_client = make_graph_client(plans)

    @asynccontextmanager
    async def _for_user(token):
        yield graph_client

    with patch("src.tools.plans.get_access_token", return_value=make_access_token()), \
         patch("src.tools.plans.graph_client_manager") as mock_mgr:
        mock_mgr.for_user = _for_user
        result = await list_my_plans()

    assert result == plans


@pytest.mark.asyncio
@pytest.mark.parametrize("get_return", [None, make_plans_result(None)], ids=["result-none", "value-none"])
async def test_returns_empty_list_when_no_plans(get_return):
    graph_client = MagicMock()
    graph_client.me.planner.plans.get = AsyncMock(return_value=get_return)

    @asynccontextmanager
    async def _for_user(token):
        yield graph_client

    with patch("src.tools.plans.get_access_token", return_value=make_access_token()), \
         patch("src.tools.plans.graph_client_manager") as mock_mgr:
        mock_mgr.for_user = _for_user
        result = await list_my_plans()

    assert result == []


# ---------------------------------------------------------------------------
# Tests: query parameter construction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("select_arg,expected", [
    ({"select": "id,title,owner,details"}, ["id", "title", "owner", "details"]),
    ({"select": "id,title"}, ["id", "title"]),
    ({}, ["id", "title", "owner", "details"]),  # default
], ids=["explicit-csv", "custom-csv", "default"])
async def test_select_is_split_into_list(select_arg, expected):
    graph_client, captured = make_capturing_client()

    @asynccontextmanager
    async def _for_user(token):
        yield graph_client

    with patch("src.tools.plans.get_access_token", return_value=make_access_token()), \
         patch("src.tools.plans.graph_client_manager") as mock_mgr:
        mock_mgr.for_user = _for_user
        await list_my_plans(**select_arg)

    assert len(captured) == 1
    assert captured[0].query_parameters.select == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("select_arg", [
    {"select": "*all"},
    {"select": None},
], ids=["star-all", "explicit-none"])
async def test_select_passes_none_when_all_fields(select_arg):
    graph_client, captured = make_capturing_client()

    @asynccontextmanager
    async def _for_user(token):
        yield graph_client

    with patch("src.tools.plans.get_access_token", return_value=make_access_token()), \
         patch("src.tools.plans.graph_client_manager") as mock_mgr:
        mock_mgr.for_user = _for_user
        await list_my_plans(**select_arg)

    assert captured[0].query_parameters.select is None


@pytest.mark.asyncio
async def test_obo_token_is_forwarded_to_for_user():
    received_tokens: list[str] = []

    @asynccontextmanager
    async def _for_user(token: str):
        received_tokens.append(token)
        yield make_graph_client([])

    with patch("src.tools.plans.get_access_token", return_value=make_access_token("my-secret-obo")), \
         patch("src.tools.plans.graph_client_manager") as mock_mgr:
        mock_mgr.for_user = _for_user
        await list_my_plans()

    assert received_tokens == ["my-secret-obo"]
