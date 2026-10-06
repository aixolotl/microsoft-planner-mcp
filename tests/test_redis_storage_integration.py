"""Integration tests for the Redis OAuth proxy store against a real Redis."""

from __future__ import annotations

import os
import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import fastmcp
import pytest
from cryptography.fernet import Fernet
from fastmcp.server.auth.oauth_proxy.models import ClientCode
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
    """A provider as one server process builds it."""
    provider = AzureProvider(
        client_id="test-client-id",
        client_secret="test-client-secret",
        tenant_id="test-tenant-id",
        base_url="http://localhost:8000",
        required_scopes=["mcp-access"],
        client_storage=make_storage(monkeypatch),
    )
    # The server calls get_routes() at startup; it initialises the JWT issuer
    # that token issuance and refresh need.
    provider.get_routes()
    return provider


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


async def test_refresh_token_issued_before_restart_refreshes_after(monkeypatch, tmp_path):
    # The acceptance criterion end to end through FastMCP's real token code:
    # tokens issued by one process, redeemed by a fresh one on the same Redis.
    # Each "process" gets its own empty FastMCP home (= a recreated container),
    # so nothing can survive via the default file store. Only the upstream
    # Entra token endpoint is stubbed.
    client = make_client(f"test-{uuid.uuid4()}")
    monkeypatch.setattr(fastmcp.settings, "home", tmp_path / "before")
    before = make_provider(monkeypatch)
    await before.register_client(client)
    # FastMCP stores this ClientCode in its browser callback after the Entra
    # sign-in; seeding it directly stands in for the interactive login.
    code = f"test-{uuid.uuid4()}"
    await before._code_store.put(
        key=code,
        value=ClientCode(
            code=code,
            client_id=client.client_id,
            redirect_uri="http://localhost:59999/callback",
            code_challenge=None,
            code_challenge_method="S256",
            scopes=["mcp-access"],
            idp_tokens={"access_token": "entra-at", "refresh_token": "entra-rt", "expires_in": 3600},
            expires_at=time.time() + 300,
            created_at=time.time(),
        ),
    )
    issued = await before.exchange_authorization_code(
        client, await before.load_authorization_code(client, code)
    )

    monkeypatch.setattr(fastmcp.settings, "home", tmp_path / "restarted")
    restarted = make_provider(monkeypatch)
    upstream = MagicMock()
    upstream.refresh_token = AsyncMock(
        return_value={"access_token": "entra-at-2", "refresh_token": "entra-rt-2", "expires_in": 3600}
    )
    with patch.object(restarted, "_create_upstream_oauth_client", return_value=upstream):
        known_client = await restarted.get_client(client.client_id)
        loaded = await restarted.load_refresh_token(known_client, issued.refresh_token)
        assert loaded is not None, "refresh token issued before the restart was lost"
        refreshed = await restarted.exchange_refresh_token(known_client, loaded, loaded.scopes)

    raw = Redis.from_url(REDIS_TEST_URL, decode_responses=True)
    await raw.delete(f"mcp-oauth-proxy-clients::{client.client_id}")
    await raw.aclose()
    # The restarted process decrypted the upstream refresh token from Redis.
    assert upstream.refresh_token.await_args.kwargs["refresh_token"] == "entra-rt"
    assert refreshed.access_token and refreshed.access_token != issued.access_token


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
