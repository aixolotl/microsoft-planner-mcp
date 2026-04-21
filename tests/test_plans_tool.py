"""Unit tests for the plans tool (list_my_plans, list_group_plans)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastmcp.exceptions import AuthorizationError
from msgraph.generated.models.planner_plan import PlannerPlan
from msgraph.generated.models.user import User

from src.tools.plans import create_plan, delete_plan, get_plan_categories, list_group_plans, list_my_plans

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


def _wire_create_plan_client(graph_client: MagicMock, plan: PlannerPlan | None = None) -> MagicMock:
    """Wire up a graph_client mock so create_plan's auto-share logic works.

    create_plan does:  POST plan → GET /me → GET plan details → PATCH sharedWith.
    Without these stubs the test raises AttributeError on the mock chains.
    """
    if plan is None:
        plan = make_plan()
    graph_client.planner.plans.post = AsyncMock(return_value=plan)

    me = User()
    me.id = "user-123"
    graph_client.me.get = AsyncMock(return_value=me)

    plan_item = graph_client.planner.plans.by_planner_plan_id.return_value
    details_mock = MagicMock()
    details_mock.additional_data = {"@odata.etag": '"details-etag-v1"'}
    plan_item.details.get = AsyncMock(return_value=details_mock)
    plan_item.details.patch = AsyncMock(return_value=None)

    return graph_client


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
        with pytest.raises(RuntimeError, match="Graph API error \\(404\\)"):
            await list_group_plans("group-1")


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
    graph_client = _wire_create_plan_client(MagicMock(), plan)

    with graph_ctx(MODULE, graph_client):
        result = await create_plan("group-1", "Sprint 1")

    assert result is plan


async def test_create_plan_posts_correct_body(graph_ctx):
    captured: list = []

    async def capturing_post(body):
        captured.append(body)
        return make_plan()

    graph_client = _wire_create_plan_client(MagicMock())
    graph_client.planner.plans.post = capturing_post

    with graph_ctx(MODULE, graph_client):
        await create_plan("group-xyz", "My Plan")

    assert len(captured) == 1
    assert isinstance(captured[0], PlannerPlan)
    assert captured[0].owner == "group-xyz"
    assert captured[0].title == "My Plan"


@pytest.mark.parametrize("status,code,exc_type", [
    (403, "AuthorizationRequestDenied", ValueError),
    (400, "BadRequest", RuntimeError),
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
        assert "Graph API error" in str(exc_info.value)


async def test_create_plan_forwards_obo_token(token_capturing_ctx):
    graph_client = _wire_create_plan_client(MagicMock())

    with token_capturing_ctx(MODULE, graph_client, "my-obo") as received:
        await create_plan("group-1", "My Plan")

    assert received == ["my-obo"]


async def test_create_plan_auto_shares_with_creator(graph_ctx):
    # After creating a plan, create_plan should PATCH the plan details to add
    # the creator to sharedWith so the plan appears in /me/planner/plans.
    plan = make_plan("new-plan-1", "Sprint 1")
    graph_client = _wire_create_plan_client(MagicMock(), plan)

    with graph_ctx(MODULE, graph_client):
        await create_plan("group-1", "Sprint 1")

    plan_item = graph_client.planner.plans.by_planner_plan_id.return_value
    plan_item.details.patch.assert_awaited_once()
    patch_body = plan_item.details.patch.call_args.args[0]
    assert patch_body.shared_with.additional_data == {"user-123": True}
    config = plan_item.details.patch.call_args.kwargs["request_configuration"]
    assert config.headers.get("if-match") == {'"details-etag-v1"'}


async def test_create_plan_returns_plan_even_if_share_fails(graph_ctx):
    # If the auto-share PATCH fails, the plan should still be returned.
    plan = make_plan("new-plan-1", "Sprint 1")
    graph_client = _wire_create_plan_client(MagicMock(), plan)
    graph_client.me.get = AsyncMock(side_effect=Exception("Share failed"))

    with graph_ctx(MODULE, graph_client):
        result = await create_plan("group-1", "Sprint 1")

    assert result is plan


async def test_create_plan_returns_none_when_post_returns_none(graph_ctx):
    graph_client = MagicMock()
    graph_client.planner.plans.post = AsyncMock(return_value=None)

    with graph_ctx(MODULE, graph_client):
        result = await create_plan("group-1", "My Plan")

    assert result is None


# ---------------------------------------------------------------------------
# Tests: delete_plan
# ---------------------------------------------------------------------------


async def test_delete_plan_calls_sdk(graph_ctx):
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.delete = AsyncMock()

    with graph_ctx(MODULE, graph_client):
        result = await delete_plan("plan-1", '"etag-v1"')

    graph_client.planner.plans.by_planner_plan_id.assert_called_once_with("plan-1")
    delete_mock = graph_client.planner.plans.by_planner_plan_id.return_value.delete
    delete_mock.assert_awaited_once()
    # Verify the ETag reaches the If-Match header on the request configuration.
    config = delete_mock.call_args.kwargs["request_configuration"]
    assert config.headers.get("if-match") == {'"etag-v1"'}

    assert result == "Deleted plan 'plan-1'."


async def test_delete_plan_forwards_obo_token(token_capturing_ctx):
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.delete = AsyncMock()

    with token_capturing_ctx(MODULE, graph_client, "my-obo") as received:
        await delete_plan("plan-1", '"etag-v1"')

    assert received == ["my-obo"]


# ---------------------------------------------------------------------------
# Tests: get_plan_categories
# ---------------------------------------------------------------------------


def make_category_descriptions(overrides: dict | None = None):
    """Build a mock PlannerCategoryDescriptions with optional custom labels."""
    from msgraph.generated.models.planner_category_descriptions import PlannerCategoryDescriptions
    obj = PlannerCategoryDescriptions()
    for i in range(1, 26):
        setattr(obj, f"category{i}", None)
    if overrides:
        for key, label in overrides.items():
            setattr(obj, key, label)
    return obj


def make_plan_details_obj(category_descriptions=None):
    """Build a mock PlannerPlanDetails with the given category_descriptions."""
    from msgraph.generated.models.planner_plan_details import PlannerPlanDetails
    details = PlannerPlanDetails()
    details.category_descriptions = category_descriptions
    return details


async def test_get_plan_categories_no_token_raises():
    with patch(f"{MODULE}.get_access_token", return_value=None):
        with pytest.raises(AuthorizationError):
            await get_plan_categories("plan-1")


async def test_get_plan_categories_returns_all_25_slots(graph_ctx):
    # All 25 category slots must be present regardless of how many have labels.
    # Integration tools rely on a predictable list length for slot indexing.
    details = make_plan_details_obj(make_category_descriptions())
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.details.get = AsyncMock(return_value=details)

    with graph_ctx(MODULE, graph_client):
        result = await get_plan_categories("plan-1")

    assert result is not None
    assert len(result) == 25
    keys = [item["key"] for item in result]
    assert "category1" in keys
    assert "category25" in keys


async def test_get_plan_categories_includes_display_names(graph_ctx):
    descriptions = make_category_descriptions({"category3": "Urgent", "category14": "Blocked"})
    details = make_plan_details_obj(descriptions)
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.details.get = AsyncMock(return_value=details)

    with graph_ctx(MODULE, graph_client):
        result = await get_plan_categories("plan-1")

    assert result is not None
    cat3 = next(c for c in result if c["key"] == "category3")
    cat14 = next(c for c in result if c["key"] == "category14")
    cat1 = next(c for c in result if c["key"] == "category1")

    assert cat3["display_name"] == "Urgent"
    assert cat14["display_name"] == "Blocked"
    assert cat1["display_name"] is None


@pytest.mark.parametrize("details_return", [None, make_plan_details_obj(None)], ids=["details-none", "category-descriptions-none"])
async def test_get_plan_categories_returns_none_when_no_details(details_return, graph_ctx):
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.details.get = AsyncMock(return_value=details_return)

    with graph_ctx(MODULE, graph_client):
        result = await get_plan_categories("plan-1")

    assert result is None


async def test_get_plan_categories_forwards_obo_token(token_capturing_ctx):
    details = make_plan_details_obj(make_category_descriptions())
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.details.get = AsyncMock(return_value=details)

    with token_capturing_ctx(MODULE, graph_client, "my-obo") as received:
        await get_plan_categories("plan-1")

    assert received == ["my-obo"]


async def test_get_plan_categories_calls_correct_plan_id(graph_ctx):
    details = make_plan_details_obj(make_category_descriptions())
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.details.get = AsyncMock(return_value=details)

    with graph_ctx(MODULE, graph_client):
        await get_plan_categories("plan-xyz")

    graph_client.planner.plans.by_planner_plan_id.assert_called_once_with("plan-xyz")


async def test_get_plan_categories_404_raises_value_error(graph_ctx, make_odata_error):
    # Graph returns 404 when a plan doesn't exist or the user can't access it.
    # A clear ValueError is raised so the LLM can suggest the caller fetch a
    # valid plan ID first, rather than surfacing a raw Graph error message.
    graph_client = MagicMock()
    graph_client.planner.plans.by_planner_plan_id.return_value.details.get = AsyncMock(
        side_effect=make_odata_error(404, "ItemNotFound", "The requested item is not found.")
    )

    with graph_ctx(MODULE, graph_client):
        with pytest.raises(ValueError, match="not found"):
            await get_plan_categories("bad-plan-id")

