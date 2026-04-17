"""Unit tests for list_my_groups tool."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from fastmcp.exceptions import AuthorizationError
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.models.group import Group
from msgraph.generated.users.item.transitive_member_of.graph_group.graph_group_request_builder import GraphGroupRequestBuilder

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

    assert result == groups


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
# Tests: $filter with advanced query headers
# ---------------------------------------------------------------------------


async def test_filter_adds_consistency_level_header(graph_ctx):
    # $filter on /me/transitiveMemberOf/microsoft.graph.group is an advanced
    # query — Graph requires ConsistencyLevel: eventual and $count=true.
    # Without these, Graph returns 400 Request_UnsupportedQuery.
    graph_client = MagicMock()
    get_mock = AsyncMock(return_value=make_groups_result([]))
    graph_client.me.transitive_member_of.graph_group.get = get_mock

    with graph_ctx(MODULE, graph_client):
        await list_my_groups(filter="startsWith(displayName,'Eng')")

    config: RequestConfiguration = get_mock.call_args.kwargs["request_configuration"]
    assert config.query_parameters.filter == "startsWith(displayName,'Eng')"
    assert config.query_parameters.count is True
    assert config.headers.get("ConsistencyLevel") == {"eventual"}


async def test_no_filter_omits_advanced_query_headers(graph_ctx):
    # When no filter is provided, advanced query headers must be absent so
    # the request stays on the default (strongly-consistent) index.
    graph_client = MagicMock()
    get_mock = AsyncMock(return_value=make_groups_result([]))
    graph_client.me.transitive_member_of.graph_group.get = get_mock

    with graph_ctx(MODULE, graph_client):
        await list_my_groups()

    config: RequestConfiguration = get_mock.call_args.kwargs["request_configuration"]
    assert config.query_parameters.filter is None
    assert config.query_parameters.count is None
    # Default RequestConfiguration headers should not contain ConsistencyLevel.
    assert config.headers.get("ConsistencyLevel") == set()


# ---------------------------------------------------------------------------
# Tests: $search with advanced query headers
# ---------------------------------------------------------------------------


async def test_search_adds_consistency_level_header(graph_ctx):
    # $search on /me/transitiveMemberOf/microsoft.graph.group is an advanced
    # query — Graph requires ConsistencyLevel: eventual and $count=true.
    # Without these, Graph returns 400 Request_UnsupportedQuery.
    graph_client = MagicMock()
    get_mock = AsyncMock(return_value=make_groups_result([]))
    graph_client.me.transitive_member_of.graph_group.get = get_mock

    with graph_ctx(MODULE, graph_client):
        await list_my_groups(search='"displayName:Project"')

    config: RequestConfiguration = get_mock.call_args.kwargs["request_configuration"]
    assert config.query_parameters.search == '"displayName:Project"'
    assert config.query_parameters.count is True
    assert config.headers.get("ConsistencyLevel") == {"eventual"}


async def test_search_and_filter_combined(graph_ctx):
    # When both $filter and $search are provided, both should appear in the
    # query parameters and the advanced query headers must still be present.
    graph_client = MagicMock()
    get_mock = AsyncMock(return_value=make_groups_result([]))
    graph_client.me.transitive_member_of.graph_group.get = get_mock

    with graph_ctx(MODULE, graph_client):
        await list_my_groups(
            filter="startsWith(displayName,'Eng')",
            search='"displayName:Project"',
        )

    config: RequestConfiguration = get_mock.call_args.kwargs["request_configuration"]
    assert config.query_parameters.filter == "startsWith(displayName,'Eng')"
    assert config.query_parameters.search == '"displayName:Project"'
    assert config.query_parameters.count is True
    assert config.headers.get("ConsistencyLevel") == {"eventual"}
