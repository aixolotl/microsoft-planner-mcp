"""Unit tests for list_my_groups tool."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from fastmcp.exceptions import AuthorizationError
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.models.group import Group
from msgraph.generated.users.item.transitive_member_of.graph_group.graph_group_request_builder import GraphGroupRequestBuilder

from src.services.base import BasePlannerService
from src.tools.groups import list_my_groups

MODULE = "src.tools.groups"
# MODULE is the import path patched by graph_ctx / token_capturing_ctx.
# It must be the module where get_access_token and graph_client_manager are
# *used* (i.e. imported into), not where they are defined. Patching the
# definition site would have no effect on the already-imported references.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_group(group_id: str = "group-1", display_name: str = "My Group") -> Group:
    group = Group()
    group.id = group_id
    group.display_name = display_name
    return group


def make_groups_result(groups) -> MagicMock:
    result = MagicMock()
    result.value = groups
    result.odata_next_link = None
    return result


# ---------------------------------------------------------------------------
# Tests: authorisation
# ---------------------------------------------------------------------------


async def test_no_token_raises():
    with patch(f"{MODULE}.get_access_token", return_value=None):
        with pytest.raises(AuthorizationError):
            await list_my_groups()


# ---------------------------------------------------------------------------
# Tests: return values
# ---------------------------------------------------------------------------


async def test_returns_group_list(graph_ctx):
    groups = [make_group("g1", "Engineering"), make_group("g2", "Product")]
    graph_client = MagicMock()
    graph_client.me.transitive_member_of.graph_group.get = AsyncMock(
        return_value=make_groups_result(groups)
    )

    with graph_ctx(MODULE, graph_client):
        result = await list_my_groups()

    assert result == BasePlannerService.serialize_graph_list(groups)


@pytest.mark.parametrize("get_return", [None, make_groups_result(None)], ids=["result-none", "value-none"])
async def test_returns_none_when_no_groups(get_return, graph_ctx):
    graph_client = MagicMock()
    graph_client.me.transitive_member_of.graph_group.get = AsyncMock(return_value=get_return)

    with graph_ctx(MODULE, graph_client):
        result = await list_my_groups()

    assert result is None


async def test_forwards_obo_token(token_capturing_ctx):
    graph_client = MagicMock()
    graph_client.me.transitive_member_of.graph_group.get = AsyncMock(
        return_value=make_groups_result([])
    )

    with token_capturing_ctx(MODULE, graph_client, "my-obo") as received:
        await list_my_groups()

    assert received == ["my-obo"]


# ---------------------------------------------------------------------------
# Tests: $select forwarding
# ---------------------------------------------------------------------------


async def test_default_select_includes_display_name_and_mail(graph_ctx):
    # The default select must include displayName and mail so that LLM clients
    # can identify groups by name. Without $select the Graph response omits
    # these fields (returns null), making the groups indistinguishable.
    graph_client = MagicMock()
    get_mock = AsyncMock(return_value=make_groups_result([]))
    graph_client.me.transitive_member_of.graph_group.get = get_mock

    with graph_ctx(MODULE, graph_client):
        await list_my_groups()

    config: RequestConfiguration = get_mock.call_args.kwargs["request_configuration"]
    assert config.query_parameters.select == ["id", "displayName", "mail"]


@pytest.mark.parametrize(
    "select_arg,expected_fields",
    [
        ("id,displayName", ["id", "displayName"]),
        ("id", ["id"]),
    ],
    ids=["two-fields", "single-field"],
)
async def test_custom_select_is_forwarded(select_arg, expected_fields, graph_ctx):
    graph_client = MagicMock()
    get_mock = AsyncMock(return_value=make_groups_result([]))
    graph_client.me.transitive_member_of.graph_group.get = get_mock

    with graph_ctx(MODULE, graph_client):
        await list_my_groups(select=select_arg)

    config: RequestConfiguration = get_mock.call_args.kwargs["request_configuration"]
    assert config.query_parameters.select == expected_fields


async def test_all_sentinel_omits_select(graph_ctx):
    # "*all" is a sentinel meaning "no $select — return every field". Without
    # this path, callers would have to pass select=None explicitly to get the
    # full Graph response, which is less ergonomic for LLM agents.
    graph_client = MagicMock()
    get_mock = AsyncMock(return_value=make_groups_result([]))
    graph_client.me.transitive_member_of.graph_group.get = get_mock

    with graph_ctx(MODULE, graph_client):
        await list_my_groups(select="*all")

    config: RequestConfiguration = get_mock.call_args.kwargs["request_configuration"]
    assert config.query_parameters.select is None


# ---------------------------------------------------------------------------
# Tests: Bug 014 — $expand errors (403, 404) convert to ValueError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status,match", [
    (403, "Insufficient privileges"),
    (404, "Navigation property not found"),
], ids=["expand-403", "expand-404"])
async def test_expand_odata_error_raises_value_error(status, match, graph_ctx, make_odata_error):
    # Bug 014: 403/404 from $expand were surfacing as raw 'Internal error'.
    # They must now be converted to ValueError with a readable message.
    graph_client = MagicMock()
    graph_client.me.transitive_member_of.graph_group.get = AsyncMock(
        side_effect=make_odata_error(status, "SomeCode", "Graph message")
    )

    with graph_ctx(MODULE, graph_client):
        with pytest.raises(ValueError, match=match):
            await list_my_groups(expand="members($select=id)")


async def test_non_400_403_404_odata_error_reraises(graph_ctx, make_odata_error):
    from msgraph.generated.models.o_data_errors.o_data_error import ODataError
    graph_client = MagicMock()
    graph_client.me.transitive_member_of.graph_group.get = AsyncMock(
        side_effect=make_odata_error(500, "ServiceError")
    )

    with graph_ctx(MODULE, graph_client):
        with pytest.raises(ODataError):
            await list_my_groups()


# ---------------------------------------------------------------------------
# Tests: Bug 016 — comma-only select resolves to zero fields after split
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_select", [", ,", ",", " , "], ids=["comma-space", "bare-comma", "space-comma-space"])
async def test_comma_only_select_raises_value_error(bad_select, graph_ctx):
    # Bug 016: strings like ", ," survive min_length=1 but split to zero tokens.
    # The post-split guard must reject them before any Graph call is made.
    graph_client = MagicMock()
    with graph_ctx(MODULE, graph_client):
        with pytest.raises(ValueError, match="resolved to no fields"):
            await list_my_groups(select=bad_select)
