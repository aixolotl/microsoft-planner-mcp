"""Unit tests for the plans tool (list_my_plans, list_group_plans)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastmcp.exceptions import AuthorizationError
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_plan import PlannerPlan

from src.tools.plans import list_group_plans, list_my_plans

MODULE = "src.tools.plans"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_plan(plan_id: str = "plan-1", title: str = "My Plan") -> PlannerPlan:
    plan = PlannerPlan()
    plan.id = plan_id
    plan.title = title
    return plan


def make_plans_result(plans) -> MagicMock:
    result = MagicMock()
    result.value = plans
    result.odata_next_link = None
    return result


def make_graph_client(plans=None) -> MagicMock:
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


@pytest.mark.parametrize("coro_fn", [
    lambda: list_my_plans(),
    lambda: list_group_plans("group-1"),
], ids=["list-my-plans", "list-group-plans"])
async def test_no_token_raises(coro_fn):
    with patch(f"{MODULE}.get_access_token", return_value=None):
        with pytest.raises(AuthorizationError):
            await coro_fn()


# ---------------------------------------------------------------------------
# Tests: return values
# ---------------------------------------------------------------------------


async def test_returns_plan_list(graph_ctx):
    plans = [make_plan("plan-1"), make_plan("plan-2")]

    with graph_ctx(MODULE, make_graph_client(plans)):
        result = await list_my_plans()

    assert result == plans


@pytest.mark.parametrize("get_return", [None, make_plans_result(None)], ids=["result-none", "value-none"])
async def test_returns_none_when_no_plans(get_return, graph_ctx):
    graph_client = MagicMock()
    graph_client.me.planner.plans.get = AsyncMock(return_value=get_return)

    with graph_ctx(MODULE, graph_client):
        result = await list_my_plans()

    assert result is None


# ---------------------------------------------------------------------------
# Tests: query parameter construction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("select_arg,expected", [
    ({"select": "id,title,owner,details"}, ["id", "title", "owner", "details"]),
    ({"select": "id,title"}, ["id", "title"]),
    ({}, ["id", "title", "owner", "details"]),  # default
], ids=["explicit-csv", "custom-csv", "default"])
async def test_select_is_split_into_list(select_arg, expected, graph_ctx):
    graph_client, captured = make_capturing_client()

    with graph_ctx(MODULE, graph_client):
        await list_my_plans(**select_arg)

    assert len(captured) == 1
    assert captured[0].query_parameters.select == expected


@pytest.mark.parametrize("select_arg", [
    {"select": "*all"},
    {"select": None},
], ids=["star-all", "explicit-none"])
async def test_select_passes_none_when_all_fields(select_arg, graph_ctx):
    graph_client, captured = make_capturing_client()

    with graph_ctx(MODULE, graph_client):
        await list_my_plans(**select_arg)

    assert len(captured) == 1
    assert captured[0].query_parameters.select is None


async def test_obo_token_is_forwarded_to_for_user(token_capturing_ctx):
    with token_capturing_ctx(MODULE, make_graph_client([]), "my-secret-obo") as received:
        await list_my_plans()

    assert received == ["my-secret-obo"]


# ---------------------------------------------------------------------------
# Tests: list_group_plans
# ---------------------------------------------------------------------------


async def test_list_group_plans_returns_plans(graph_ctx):
    plans = [make_plan("p1"), make_plan("p2")]
    graph_client = MagicMock()
    graph_client.groups.by_group_id.return_value.planner.plans.get = AsyncMock(
        return_value=make_plans_result(plans)
    )

    with graph_ctx(MODULE, graph_client):
        result = await list_group_plans("group-1")

    assert result == plans
    graph_client.groups.by_group_id.assert_called_once_with("group-1")


@pytest.mark.parametrize("get_return", [None, make_plans_result(None)], ids=["result-none", "value-none"])
async def test_list_group_plans_returns_none_when_empty(get_return, graph_ctx):
    graph_client = MagicMock()
    graph_client.groups.by_group_id.return_value.planner.plans.get = AsyncMock(return_value=get_return)

    with graph_ctx(MODULE, graph_client):
        result = await list_group_plans("group-1")

    assert result is None


async def test_list_group_plans_403_raises_value_error(graph_ctx, make_odata_error):
    graph_client = MagicMock()
    graph_client.groups.by_group_id.return_value.planner.plans.get = AsyncMock(
        side_effect=make_odata_error(403, "AuthorizationRequestDenied", "Access denied")
    )

    with graph_ctx(MODULE, graph_client):
        with pytest.raises(ValueError, match="Access denied for group 'group-1'"):
            await list_group_plans("group-1")


async def test_list_group_plans_403_error_message_includes_code(graph_ctx, make_odata_error):
    graph_client = MagicMock()
    graph_client.groups.by_group_id.return_value.planner.plans.get = AsyncMock(
        side_effect=make_odata_error(403, "AuthorizationRequestDenied", "Insufficient privileges")
    )

    with graph_ctx(MODULE, graph_client):
        with pytest.raises(ValueError, match="AuthorizationRequestDenied"):
            await list_group_plans("group-1")


async def test_list_group_plans_non_403_odata_error_reraises(graph_ctx, make_odata_error):
    graph_client = MagicMock()
    graph_client.groups.by_group_id.return_value.planner.plans.get = AsyncMock(
        side_effect=make_odata_error(404, "ResourceNotFound")
    )

    with graph_ctx(MODULE, graph_client):
        with pytest.raises(ODataError) as exc_info:
            await list_group_plans("group-1")

    assert exc_info.value.response_status_code == 404


async def test_list_group_plans_forwards_obo_token(token_capturing_ctx):
    graph_client = MagicMock()
    graph_client.groups.by_group_id.return_value.planner.plans.get = AsyncMock(
        return_value=make_plans_result([])
    )

    with token_capturing_ctx(MODULE, graph_client, "my-obo") as received:
        await list_group_plans("group-1")

    assert received == ["my-obo"]
