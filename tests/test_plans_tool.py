"""Unit tests for the plans tool (list_my_plans, list_group_plans)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastmcp.exceptions import AuthorizationError
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_plan import PlannerPlan

from src.tools.plans import create_plan, delete_plan, list_group_plans, list_my_plans

MODULE = "src.tools.plans"
# MODULE is the import path patched by graph_ctx / token_capturing_ctx.
# It must be the module where get_access_token and graph_client_manager are
# *used* (i.e. imported into), not where they are defined. Patching the
# definition site would have no effect on the already-imported references.


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
    lambda: create_plan("group-1", "My Plan"),
    lambda: delete_plan("plan-1", '"etag-v1"'),
], ids=["list-my-plans", "list-group-plans", "create-plan", "delete-plan"])
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
    ({"select": "id,title,owner,createdBy,createdDateTime"}, ["id", "title", "owner", "createdBy", "createdDateTime"]),
    ({"select": "id,title"}, ["id", "title"]),
    ({}, ["id", "title", "owner", "createdBy", "createdDateTime"]),  # default value
    ({"select": "*all"}, None),
    ({"select": None}, None),
], ids=["explicit-csv", "custom-csv", "default", "star-all", "explicit-none"])
async def test_select(select_arg, expected, graph_ctx):
    graph_client, captured = make_capturing_client()

    with graph_ctx(MODULE, graph_client):
        await list_my_plans(**select_arg)

    assert captured[0].query_parameters.select == expected


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


@pytest.mark.parametrize("code,msg,match", [
    ("AuthorizationRequestDenied", "Access denied", "Access denied for group 'group-1'"),
    ("AuthorizationRequestDenied", "Insufficient privileges", "AuthorizationRequestDenied"),
], ids=["403-includes-group-id", "403-includes-error-code"])
async def test_list_group_plans_403_raises_value_error(code, msg, match, graph_ctx, make_odata_error):
    graph_client = MagicMock()
    graph_client.groups.by_group_id.return_value.planner.plans.get = AsyncMock(
        side_effect=make_odata_error(403, code, msg)
    )

    with graph_ctx(MODULE, graph_client):
        with pytest.raises(ValueError, match=match):
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


# ---------------------------------------------------------------------------
# Tests: create_plan
# ---------------------------------------------------------------------------


async def test_create_plan_returns_plan(graph_ctx):
    plan = make_plan("new-plan-1", "Sprint 1")
    graph_client = MagicMock()
    graph_client.planner.plans.post = AsyncMock(return_value=plan)

    with graph_ctx(MODULE, graph_client):
        result = await create_plan("group-1", "Sprint 1")

    assert result is plan


async def test_create_plan_posts_correct_body(graph_ctx):
    captured: list = []

    async def capturing_post(body):
        captured.append(body)
        return make_plan()

    graph_client = MagicMock()
    graph_client.planner.plans.post = capturing_post

    with graph_ctx(MODULE, graph_client):
        await create_plan("group-xyz", "My Plan")

    assert len(captured) == 1
    assert isinstance(captured[0], PlannerPlan)
    assert captured[0].owner == "group-xyz"
    assert captured[0].title == "My Plan"


@pytest.mark.parametrize("status,code,exc_type", [
    (403, "AuthorizationRequestDenied", ValueError),
    (400, "BadRequest", ODataError),
], ids=["403-value-error", "400-reraises"])
async def test_create_plan_odata_error(status, code, exc_type, graph_ctx, make_odata_error):
    graph_client = MagicMock()
    graph_client.planner.plans.post = AsyncMock(side_effect=make_odata_error(status, code))

    with graph_ctx(MODULE, graph_client):
        with pytest.raises(exc_type) as exc_info:
            await create_plan("group-1", "My Plan")

    if exc_type is ValueError:
        assert "Cannot create plan" in str(exc_info.value)
    else:
        assert exc_info.value.response_status_code == status


async def test_create_plan_forwards_obo_token(token_capturing_ctx):
    graph_client = MagicMock()
    graph_client.planner.plans.post = AsyncMock(return_value=make_plan())

    with token_capturing_ctx(MODULE, graph_client, "my-obo") as received:
        await create_plan("group-1", "My Plan")

    assert received == ["my-obo"]


# ---------------------------------------------------------------------------
# Tests: delete_plan
# ---------------------------------------------------------------------------


async def test_delete_plan_calls_sdk(graph_ctx):
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.delete = AsyncMock()

    with graph_ctx(MODULE, graph_client):
        await delete_plan("plan-1", '"etag-v1"')

    graph_client.planner.plans.by_planner_plan_id.assert_called_once_with("plan-1")
    graph_client.planner.plans.by_planner_plan_id.return_value.delete.assert_awaited_once()


async def test_delete_plan_forwards_obo_token(token_capturing_ctx):
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.delete = AsyncMock()

    with token_capturing_ctx(MODULE, graph_client, "my-obo") as received:
        await delete_plan("plan-1", '"etag-v1"')

    assert received == ["my-obo"]
