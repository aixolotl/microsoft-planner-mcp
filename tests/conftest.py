"""Shared pytest fixtures for all test modules."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager, contextmanager
from unittest.mock import MagicMock, patch

import pytest

from msgraph.generated.models.o_data_errors.main_error import MainError
from msgraph.generated.models.o_data_errors.o_data_error import ODataError

from src.services.planner_service import PlannerService


def pytest_configure(config: pytest.Config) -> None:
    """Set required env vars before any src module is imported.

    WHY THIS EXISTS:
    src/config.py instantiates Settings() at module level. pydantic-settings
    validates CLIENT_ID, CLIENT_SECRET, and TENANT_ID as required fields at
    that point. Without real env vars or a .env file the import fails during
    collection — before any fixture or monkeypatch can intervene.

    pytest_configure() is the earliest hook pytest provides; it runs before
    collection begins, so the fake values are in os.environ when Python first
    imports src.config. os.environ.setdefault() is used so that if a developer
    has a real .env-exported value in their shell it takes precedence.
    """
    os.environ.setdefault("CLIENT_ID", "test-client-id")
    os.environ.setdefault("CLIENT_SECRET", "test-client-secret")
    os.environ.setdefault("TENANT_ID", "test-tenant-id")


@pytest.fixture(autouse=True)
def _disable_serialize(monkeypatch):
    """Default PlannerService.serialize to False in all tests.

    WHY THIS EXISTS:
    Tools now always serialise Graph SDK objects to dicts via PlannerService.
    Tool-level tests are concerned with auth, delegation, and error handling —
    not with Kiota JSON serialisation. Defaulting serialize=False keeps tool
    tests decoupled from the serialisation format. Tests that specifically need
    to verify serialisation behaviour can override with serialize=True.

    Docs: https://learn.microsoft.com/en-us/openapi/kiota/serialization
    """
    _orig_init = PlannerService.__init__

    def _patched_init(self, client, *, serialize=False):
        _orig_init(self, client, serialize=serialize)

    monkeypatch.setattr(PlannerService, "__init__", _patched_init)


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

    WHY THIS EXISTS:
    Every tool calls get_access_token() and graph_client_manager.for_user() at
    the top of its body. In production these are resolved from the FastMCP
    request context and the Azure OBO flow. In tests we have neither, so we
    patch both at the module level where the tool imports them. Patching at the
    module level (not the definition site) is required because the tool already
    holds a reference to the imported name at import time.

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

    WHY THIS EXISTS:
    graph_ctx only verifies that a Graph call succeeds; it does not assert which
    OBO token was forwarded to GraphClientManager. token_capturing_ctx records
    the exact token string(s) passed to for_user so tests can assert that the
    tool correctly threads the user’s token through to the OBO exchange —
    without this assertion it would be possible to accidentally hard-code a
    token or forward the wrong one without any test catching it.

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
