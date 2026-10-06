"""Integration tests for the Redis OAuth proxy store against a real Redis."""

from __future__ import annotations

import os
import uuid
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from fastmcp.server.auth.providers.azure import AzureProvider
from mcp.shared.auth import OAuthClientInformationFull
from redis.asyncio import Redis

from src import auth_provider as auth_module
from src import config as config_module

# A mocked store cannot show that state written by one server process is read
# back by the next, or that Redis applies FastMCP's TTLs — only a real Redis
# can. CI provides one as a service container; locally, point this at any
# scratch DB (e.g. redis://localhost:6379/15). Without it the tests skip.
# Docs: https://docs.github.com/en/actions/use-cases-and-examples/using-containerized-services/creating-redis-service-containers
REDIS_TEST_URL = os.environ.get("REDIS_TEST_URL")
pytestmark = pytest.mark.skipif(
    not REDIS_TEST_URL, reason="set REDIS_TEST_URL to run against a real Redis"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_storage(monkeypatch):
    """The client storage a freshly started server process would build."""
    monkeypatch.setenv("REDIS_URL", REDIS_TEST_URL)
    settings = config_module.Settings(_env_file=None)
    with patch.object(auth_module, "settings", settings):
        return auth_module._redis_client_storage()


def make_provider(monkeypatch) -> AzureProvider:
    return AzureProvider(
        client_id="test-client-id",
        client_secret="test-client-secret",
        tenant_id="test-tenant-id",
        base_url="http://localhost:8000",
        required_scopes=["mcp-access"],
        client_storage=make_storage(monkeypatch),
    )


def make_client(client_id: str) -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        redirect_uris=["http://localhost:59999/callback"],
        grant_types=["authorization_code", "refresh_token"],
        token_endpoint_auth_method="none",
    )


# ---------------------------------------------------------------------------
# Tests: state survives a server restart
# ---------------------------------------------------------------------------


async def test_client_registered_before_restart_is_known_after(monkeypatch):
    # Losing this registration is the reported failure: every refresh after a
    # container recreation returned 401 invalid_client.
    client_id = f"test-{uuid.uuid4()}"
    await make_provider(monkeypatch).register_client(make_client(client_id))

    restarted = make_provider(monkeypatch)

    found = await restarted.get_client(client_id)
    # FastMCP's default file store would also survive an in-process "restart",
    # so pin that the registration really lives in Redis.
    raw = Redis.from_url(REDIS_TEST_URL, decode_responses=True)
    try:
        in_redis = await raw.exists(f"mcp-oauth-proxy-clients::{client_id}")
    finally:
        await raw.delete(f"mcp-oauth-proxy-clients::{client_id}")
        await raw.aclose()
    assert found is not None and found.client_id == client_id
    assert in_redis == 1


# ---------------------------------------------------------------------------
# Tests: what lands in Redis
# ---------------------------------------------------------------------------


async def test_entries_are_encrypted_and_honour_ttl(monkeypatch):
    # The store holds users' Entra refresh tokens: never in plaintext, and
    # short-lived entries must expire (FastMCP passes ttl= on every put).
    key = f"test-{uuid.uuid4()}"
    await make_storage(monkeypatch).put(
        key=key, value={"refresh_token": "entra-rt-plaintext"}, collection="mcp-upstream-tokens", ttl=60
    )

    raw = Redis.from_url(REDIS_TEST_URL, decode_responses=True)
    try:
        stored = await raw.get(f"mcp-upstream-tokens::{key}")
        ttl = await raw.ttl(f"mcp-upstream-tokens::{key}")
    finally:
        await raw.delete(f"mcp-upstream-tokens::{key}")
        await raw.aclose()

    assert "__encrypted_data__" in stored
    assert "entra-rt-plaintext" not in stored
    assert 0 < ttl <= 60


async def test_rotated_encryption_key_reads_as_a_miss(monkeypatch):
    # After a key change, old entries must read as "not found" (the client
    # gets 401 and reconnects) rather than raise and turn /token into a 500.
    key = f"test-{uuid.uuid4()}"
    monkeypatch.setenv("STORAGE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    await make_storage(monkeypatch).put(key=key, value={"v": 1}, collection="mcp-test")

    monkeypatch.setenv("STORAGE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    try:
        found = await make_storage(monkeypatch).get(key=key, collection="mcp-test")
    finally:
        raw = Redis.from_url(REDIS_TEST_URL, decode_responses=True)
        await raw.delete(f"mcp-test::{key}")
        await raw.aclose()

    assert found is None
