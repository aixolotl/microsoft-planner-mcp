"""Unit tests for list_my_groups tool."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastmcp.exceptions import AuthorizationError
from msgraph.generated.models.group import Group

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
