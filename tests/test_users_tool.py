"""Unit tests for list_users tool."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastmcp.exceptions import AuthorizationError
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.models.user import User
from msgraph.generated.users.users_request_builder import UsersRequestBuilder

from src.tools.users import _build_id_filter, _normalize_search, list_users

# Patch at the usage module, not the definition module. Python resolves the
# imported reference when the tool module is first loaded; patching the
# definition site after that has no effect on the already-bound name.
MODULE = "src.tools.users"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_user(
    user_id: str = "user-1",
    display_name: str = "Alice Smith",
    mail: str = "alice@example.com",
    upn: str = "alice@example.com",
) -> User:
    user = User()
    user.id = user_id
    user.display_name = display_name
    user.mail = mail
    user.user_principal_name = upn
    return user


def make_users_result(users) -> MagicMock:
    result = MagicMock()
    result.value = users
    result.odata_next_link = None
    return result


# ---------------------------------------------------------------------------
# Tests: authorisation
# ---------------------------------------------------------------------------


async def test_no_token_raises():
    with patch(f"{MODULE}.get_access_token", return_value=None):
        with pytest.raises(AuthorizationError):
            await list_users()


# ---------------------------------------------------------------------------
# Tests: return values
# ---------------------------------------------------------------------------


async def test_returns_user_list(graph_ctx):
    users = [make_user("u1", "Alice"), make_user("u2", "Bob")]
    graph_client = MagicMock()
    graph_client.users.get = AsyncMock(return_value=make_users_result(users))

    with graph_ctx(MODULE, graph_client):
        result = await list_users()

    assert result == users


@pytest.mark.parametrize(
    "get_return",
    [None, make_users_result(None)],
    ids=["result-none", "value-none"],
)
async def test_returns_none_when_no_users(get_return, graph_ctx):
    # Both a None result and a result with value=None should surface as None
    # from the tool. Without testing both shapes, a regression could slip
    # through where one case returns None and the other returns an empty list.
    graph_client = MagicMock()
    graph_client.users.get = AsyncMock(return_value=get_return)

    with graph_ctx(MODULE, graph_client):
        result = await list_users()

    assert result is None


async def test_forwards_obo_token(token_capturing_ctx):
    graph_client = MagicMock()
    graph_client.users.get = AsyncMock(return_value=make_users_result([]))

    with token_capturing_ctx(MODULE, graph_client, "my-obo") as received:
        await list_users()

    assert received == ["my-obo"]


# ---------------------------------------------------------------------------
# Tests: default mode ($select + $top, no advanced headers)
# ---------------------------------------------------------------------------


async def test_default_select_fields(graph_ctx):
    # The default select must include displayName so LLM clients can identify
    # users by name. Without $select the Graph response omits these fields.
    graph_client = MagicMock()
    get_mock = AsyncMock(return_value=make_users_result([]))
    graph_client.users.get = get_mock

    with graph_ctx(MODULE, graph_client):
        await list_users()

    config: RequestConfiguration = get_mock.call_args.kwargs["request_configuration"]
    assert config.query_parameters.select == [
        "id",
        "displayName",
        "mail",
        "userPrincipalName",
    ]


async def test_default_top_is_ten(graph_ctx):
    graph_client = MagicMock()
    get_mock = AsyncMock(return_value=make_users_result([]))
    graph_client.users.get = get_mock

    with graph_ctx(MODULE, graph_client):
        await list_users()

    config: RequestConfiguration = get_mock.call_args.kwargs["request_configuration"]
    assert config.query_parameters.top == 10


async def test_custom_top_is_forwarded(graph_ctx):
    graph_client = MagicMock()
    get_mock = AsyncMock(return_value=make_users_result([]))
    graph_client.users.get = get_mock

    with graph_ctx(MODULE, graph_client):
        await list_users(top=25)

    config: RequestConfiguration = get_mock.call_args.kwargs["request_configuration"]
    assert config.query_parameters.top == 25


async def test_all_sentinel_omits_select(graph_ctx):
    # "*all" is a sentinel meaning "no $select — return every field". Without
    # this path, callers would have to pass select=None explicitly.
    graph_client = MagicMock()
    get_mock = AsyncMock(return_value=make_users_result([]))
    graph_client.users.get = get_mock

    with graph_ctx(MODULE, graph_client):
        await list_users(select="*all")

    config: RequestConfiguration = get_mock.call_args.kwargs["request_configuration"]
    assert config.query_parameters.select is None


async def test_default_mode_has_no_consistency_level_header(graph_ctx):
    # Default (no filter / search) should use the strongly-consistent index.
    # Adding ConsistencyLevel: eventual unnecessarily can change result ordering.
    graph_client = MagicMock()
    get_mock = AsyncMock(return_value=make_users_result([]))
    graph_client.users.get = get_mock

    with graph_ctx(MODULE, graph_client):
        await list_users()

    config: RequestConfiguration = get_mock.call_args.kwargs["request_configuration"]
    assert config.headers.get("ConsistencyLevel") == set()


# ---------------------------------------------------------------------------
# Tests: $search mode
# ---------------------------------------------------------------------------


async def test_search_sends_search_param_and_advanced_headers(graph_ctx):
    # $search on /users is an advanced query requiring ConsistencyLevel: eventual
    # and $count=true. Without these Graph returns 400 Request_UnsupportedQuery.
    # Docs: https://learn.microsoft.com/en-us/graph/aad-advanced-queries
    graph_client = MagicMock()
    get_mock = AsyncMock(return_value=make_users_result([]))
    graph_client.users.get = get_mock

    with graph_ctx(MODULE, graph_client):
        await list_users(search='"displayName:Alice"')

    config: RequestConfiguration = get_mock.call_args.kwargs["request_configuration"]
    assert config.query_parameters.search == '"displayName:Alice"'
    assert config.query_parameters.count is True
    assert config.headers.get("ConsistencyLevel") == {"eventual"}


async def test_search_respects_top(graph_ctx):
    graph_client = MagicMock()
    get_mock = AsyncMock(return_value=make_users_result([]))
    graph_client.users.get = get_mock

    with graph_ctx(MODULE, graph_client):
        await list_users(search='"surname:Smith"', top=5)

    config: RequestConfiguration = get_mock.call_args.kwargs["request_configuration"]
    assert config.query_parameters.top == 5


# ---------------------------------------------------------------------------
# Tests: $filter mode (guids / emails)
# ---------------------------------------------------------------------------


async def test_guids_build_filter_expression(graph_ctx):
    # Providing guids should build a $filter with id eq '...' clauses so Graph
    # resolves the exact users. Without this, the tool would fall through to the
    # default list mode and miss the requested users entirely.
    graph_client = MagicMock()
    get_mock = AsyncMock(return_value=make_users_result([]))
    graph_client.users.get = get_mock

    with graph_ctx(MODULE, graph_client):
        await list_users(guids=["guid-1", "guid-2"])

    config: RequestConfiguration = get_mock.call_args.kwargs["request_configuration"]
    assert config.query_parameters.filter == "id eq 'guid-1' or id eq 'guid-2'"


async def test_emails_build_filter_expression(graph_ctx):
    graph_client = MagicMock()
    get_mock = AsyncMock(return_value=make_users_result([]))
    graph_client.users.get = get_mock

    with graph_ctx(MODULE, graph_client):
        await list_users(emails=["alice@example.com"])

    config: RequestConfiguration = get_mock.call_args.kwargs["request_configuration"]
    assert config.query_parameters.filter == "userPrincipalName eq 'alice@example.com'"


async def test_guids_and_emails_combined_in_filter(graph_ctx):
    graph_client = MagicMock()
    get_mock = AsyncMock(return_value=make_users_result([]))
    graph_client.users.get = get_mock

    with graph_ctx(MODULE, graph_client):
        await list_users(guids=["guid-1"], emails=["bob@example.com"])

    config: RequestConfiguration = get_mock.call_args.kwargs["request_configuration"]
    assert config.query_parameters.filter == (
        "id eq 'guid-1' or userPrincipalName eq 'bob@example.com'"
    )


async def test_filter_mode_adds_consistency_level_header(graph_ctx):
    # $filter by id on /users is an advanced query requiring ConsistencyLevel:
    # eventual + $count=true. Without these Graph returns 400.
    # Docs: https://learn.microsoft.com/en-us/graph/aad-advanced-queries
    graph_client = MagicMock()
    get_mock = AsyncMock(return_value=make_users_result([]))
    graph_client.users.get = get_mock

    with graph_ctx(MODULE, graph_client):
        await list_users(guids=["guid-1"])

    config: RequestConfiguration = get_mock.call_args.kwargs["request_configuration"]
    assert config.query_parameters.count is True
    assert config.headers.get("ConsistencyLevel") == {"eventual"}


async def test_filter_mode_ignores_top_param(graph_ctx):
    # When guids/emails are provided the caller wants specific users, not a
    # page-limited list. The $top parameter should not be included so all
    # matching users are returned.
    graph_client = MagicMock()
    get_mock = AsyncMock(return_value=make_users_result([]))
    graph_client.users.get = get_mock

    with graph_ctx(MODULE, graph_client):
        await list_users(guids=["guid-1"], top=5)

    config: RequestConfiguration = get_mock.call_args.kwargs["request_configuration"]
    assert config.query_parameters.top is None


async def test_guids_takes_priority_over_search(graph_ctx):
    # When both guids and search are supplied, guids/emails mode wins. This
    # avoids mixing $filter and $search semantics in a single request (Graph
    # does not support combining them on the users endpoint).
    graph_client = MagicMock()
    get_mock = AsyncMock(return_value=make_users_result([]))
    graph_client.users.get = get_mock

    with graph_ctx(MODULE, graph_client):
        await list_users(guids=["guid-1"], search='"displayName:Alice"')

    config: RequestConfiguration = get_mock.call_args.kwargs["request_configuration"]
    assert config.query_parameters.filter is not None
    assert config.query_parameters.search is None


# ---------------------------------------------------------------------------
# Tests: _build_id_filter helper
# ---------------------------------------------------------------------------


def test_build_id_filter_guids_only():
    result = _build_id_filter(["a", "b"], None)
    assert result == "id eq 'a' or id eq 'b'"


def test_build_id_filter_emails_only():
    result = _build_id_filter(None, ["x@y.com"])
    assert result == "userPrincipalName eq 'x@y.com'"


def test_build_id_filter_combined():
    result = _build_id_filter(["a"], ["x@y.com"])
    assert result == "id eq 'a' or userPrincipalName eq 'x@y.com'"


def test_build_id_filter_truncates_at_2048_chars():
    # Each GUID clause is "id eq 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'" (44 chars).
    # Generate enough GUIDs to exceed 2 048 characters and verify the result is capped.
    guid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    many_guids = [guid] * 100
    result = _build_id_filter(many_guids, None)
    assert len(result) <= 2048


# ---------------------------------------------------------------------------
# Tests: _normalize_search helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Plain string — defaults to displayName
        ("alice", '"displayName:alice"'),
        # Already-quoted plain string — quotes stripped, still defaults to displayName
        ('"alice"', '"displayName:alice"'),
        ("'alice'", '"displayName:alice"'),
        # field:value syntax — kept as-is, re-wrapped
        ("surname:Smith", '"surname:Smith"'),
        ('"surname:Smith"', '"surname:Smith"'),
        # Explicit displayName field — unchanged
        ("displayName:Bob", '"displayName:Bob"'),
        ('"displayName:Bob"', '"displayName:Bob"'),
        # Extra whitespace is stripped
        ("  alice  ", '"displayName:alice"'),
    ],
    ids=[
        "plain-string",
        "double-quoted-plain",
        "single-quoted-plain",
        "field-value-no-quotes",
        "field-value-quoted",
        "explicit-displayname-no-quotes",
        "explicit-displayname-quoted",
        "leading-trailing-whitespace",
    ],
)
def test_normalize_search(raw, expected):
    assert _normalize_search(raw) == expected


# ---------------------------------------------------------------------------
# Tests: search normalization in list_users
# ---------------------------------------------------------------------------


async def test_search_plain_string_defaults_to_display_name(graph_ctx):
    # A plain search term without a field prefix should be sent to Graph as
    # "displayName:<term>" so callers don't need to know the OData $search syntax.
    graph_client = MagicMock()
    get_mock = AsyncMock(return_value=make_users_result([]))
    graph_client.users.get = get_mock

    with graph_ctx(MODULE, graph_client):
        await list_users(search="alice")

    config: RequestConfiguration = get_mock.call_args.kwargs["request_configuration"]
    assert config.query_parameters.search == '"displayName:alice"'


async def test_search_field_prefix_is_preserved(graph_ctx):
    # When the caller provides a field:value string, the field must not be
    # overridden with displayName. Without this, "surname:Smith" would become
    # "displayName:surname:Smith" and Graph would return no results.
    graph_client = MagicMock()
    get_mock = AsyncMock(return_value=make_users_result([]))
    graph_client.users.get = get_mock

    with graph_ctx(MODULE, graph_client):
        await list_users(search="surname:Smith")

    config: RequestConfiguration = get_mock.call_args.kwargs["request_configuration"]
    assert config.query_parameters.search == '"surname:Smith"'

