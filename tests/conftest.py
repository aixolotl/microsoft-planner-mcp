"""Shared pytest fixtures for all test modules."""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from unittest.mock import MagicMock, patch

import pytest

from msgraph.generated.models.o_data_errors.main_error import MainError
from msgraph.generated.models.o_data_errors.o_data_error import ODataError


@pytest.fixture
def make_access_token():
    """Factory fixture: creates a mock access token."""
    def _make(token_str: str = "test-obo-token") -> MagicMock:
        token = MagicMock()
        token.token = token_str
        return token
    return _make


@pytest.fixture
def make_odata_error():
    """Factory fixture: creates an ODataError with a given status, code, and message."""
    def _make(status: int, code: str = "SomeCode", message: str = "Some message") -> ODataError:
        err = ODataError()
        err.response_status_code = status
        err.error = MainError(code=code, message=message)
        return err
    return _make


@pytest.fixture
def graph_ctx():
    """Factory fixture: returns a context manager that patches get_access_token and
    graph_client_manager.for_user for the given module, yielding the supplied graph_client.

    Usage::

        with graph_ctx("src.tools.tasks", graph_client):
            result = await some_tool(...)

        # optionally stack with another patch:
        with graph_ctx("src.tools.tasks", MagicMock()), patch("src.tools.tasks.PlannerService", return_value=svc):
            ...
    """
    @contextmanager
    def _ctx(module: str, graph_client: MagicMock, token_str: str = "test-obo-token"):
        token = MagicMock()
        token.token = token_str

        @asynccontextmanager
        async def _for_user(_):
            yield graph_client

        with patch(f"{module}.get_access_token", return_value=token), \
             patch(f"{module}.graph_client_manager") as mock_mgr:
            mock_mgr.for_user = _for_user
            yield

    return _ctx


@pytest.fixture
def token_capturing_ctx():
    """Factory fixture: patches get_access_token and graph_client_manager.for_user for the
    given module, captures every token string passed to for_user, and yields the list.

    Usage::

        async def test_obo_token_forwarded(token_capturing_ctx):
            graph_client = MagicMock()
            graph_client.me.planner.tasks.get = AsyncMock(return_value=...)

            with token_capturing_ctx(MODULE, graph_client, "my-obo") as received:
                await some_tool()

            assert received == ["my-obo"]
    """
    @contextmanager
    def _ctx(module: str, graph_client: MagicMock, token_str: str = "my-obo"):
        received: list[str] = []
        token = MagicMock()
        token.token = token_str

        @asynccontextmanager
        async def _for_user(t: str):
            received.append(t)
            yield graph_client

        with patch(f"{module}.get_access_token", return_value=token), \
             patch(f"{module}.graph_client_manager") as mock_mgr:
            mock_mgr.for_user = _for_user
            yield received

    return _ctx
