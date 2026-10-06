"""Unit tests for auth provider configuration."""

from __future__ import annotations

import importlib
import sys
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from fastmcp.server.auth.jwt_issuer import derive_jwt_key
from key_value.aio.stores.redis import RedisStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

from src import config as config_module

MODULE = "src.auth_provider"
# MODULE is reloaded after patching because AzureProvider is constructed at
# import time. Without re-importing the usage module, the test would inspect an
# already-created provider and miss whether new settings are forwarded.
# Docs: https://docs.python.org/3/library/importlib.html#importlib.reload

FERNET_KEY = "fXpQ0Ul6ZJ8fKk5q8D0v0b7n7cK4l9sQ3m2a1b0c9d8="
STORAGE_ENV = ("REDIS_URL", "JWT_SIGNING_KEY", "STORAGE_ENCRYPTION_KEY")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_provider_kwargs(monkeypatch, **env):
    """Reload src.auth_provider under the given env; return AzureProvider's kwargs."""
    for name in STORAGE_ENV:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    settings = config_module.Settings(_env_file=None)
    original_settings = config_module.settings
    # Import BEFORE patching: a first import_module inside the patch would
    # execute the module, and the reload execute it again — two calls.
    module = importlib.import_module(MODULE)
    try:
        with patch.object(config_module, "settings", settings), patch(
            "fastmcp.server.auth.providers.azure.AzureProvider"
        ) as mock_provider:
            importlib.reload(module)
        mock_provider.assert_called_once()
        return mock_provider.call_args.kwargs
    finally:
        config_module.settings = original_settings
        importlib.reload(sys.modules[MODULE])


# ---------------------------------------------------------------------------
# Tests: settings
# ---------------------------------------------------------------------------


def test_settings_default_require_authorization_consent_is_true(monkeypatch):
    monkeypatch.delenv("REQUIRE_AUTHORIZATION_CONSENT", raising=False)
    monkeypatch.delenv("require_authorization_consent", raising=False)

    settings = config_module.Settings(_env_file=None)

    assert settings.REQUIRE_AUTHORIZATION_CONSENT is True


def test_settings_reads_require_authorization_consent_env(monkeypatch):
    monkeypatch.delenv("REQUIRE_AUTHORIZATION_CONSENT", raising=False)
    monkeypatch.setenv("REQUIRE_AUTHORIZATION_CONSENT", "false")

    settings = config_module.Settings(_env_file=None)

    assert settings.REQUIRE_AUTHORIZATION_CONSENT is False


def test_settings_reads_lowercase_require_authorization_consent_env(monkeypatch):
    monkeypatch.delenv("REQUIRE_AUTHORIZATION_CONSENT", raising=False)
    monkeypatch.setenv("require_authorization_consent", "false")

    settings = config_module.Settings(_env_file=None)

    assert settings.REQUIRE_AUTHORIZATION_CONSENT is False


# ---------------------------------------------------------------------------
# Tests: auth provider wiring
# ---------------------------------------------------------------------------


def test_auth_provider_passes_configured_require_authorization_consent(monkeypatch):
    monkeypatch.delenv("REQUIRE_AUTHORIZATION_CONSENT", raising=False)

    kwargs = make_provider_kwargs(monkeypatch, require_authorization_consent="false")

    assert kwargs["require_authorization_consent"] is False


# ---------------------------------------------------------------------------
# Tests: OAuth proxy state storage wiring
# ---------------------------------------------------------------------------


def test_no_redis_url_keeps_fastmcp_default_store(monkeypatch):
    # client_storage=None / jwt_signing_key=None is exactly today's build:
    # FastMCP's encrypted file store and a key derived from CLIENT_SECRET.
    kwargs = make_provider_kwargs(monkeypatch)

    assert kwargs["client_storage"] is None
    assert kwargs["jwt_signing_key"] is None


@pytest.mark.parametrize(
    "url,connection_class,db",
    [
        ("redis://localhost:6379/1", "Connection", 1),
        ("rediss://:pw@example.redis.cache.windows.net:6380/2", "SSLConnection", 2),
    ],
    ids=["redis-plain", "rediss-tls"],
)
def test_redis_url_wires_encrypted_redis_store(monkeypatch, url, connection_class, db):
    # RedisStore(url=...) rebuilds the client from host/port/db/password and
    # drops TLS, so a rediss:// URL must still yield an SSLConnection.
    kwargs = make_provider_kwargs(monkeypatch, REDIS_URL=url)

    storage = kwargs["client_storage"]
    assert isinstance(storage, FernetEncryptionWrapper)
    assert storage.raise_on_decryption_error is False
    assert isinstance(storage.key_value, RedisStore)
    pool = storage.key_value._client.connection_pool
    assert pool.connection_class.__name__ == connection_class
    assert pool.connection_kwargs["db"] == db


def test_redis_client_bounds_outage_latency(monkeypatch):
    # Every tool call reads the token mapping from Redis. Left to redis-py's
    # implicit defaults (5 s timeouts, up to 10 retries), a black-holed Redis
    # would stall every request; pin a short, explicit budget instead.
    kwargs = make_provider_kwargs(monkeypatch, REDIS_URL="redis://localhost:6379/1")

    pool = kwargs["client_storage"].key_value._client.connection_pool
    assert pool.connection_kwargs["socket_connect_timeout"] == 2
    assert pool.connection_kwargs["socket_timeout"] == 2
    assert pool.connection_kwargs["retry"].get_retries() == 2


def test_jwt_signing_key_is_forwarded_derived_once(monkeypatch):
    # Bytes are used verbatim by FastMCP; a string would make it re-run the
    # same 1M-iteration PBKDF2. Same derivation, so the signing key is unchanged.
    kwargs = make_provider_kwargs(
        monkeypatch, JWT_SIGNING_KEY="a-fixed-signing-secret-of-at-least-32-chars"
    )

    assert kwargs["jwt_signing_key"] == derive_jwt_key(
        low_entropy_material="a-fixed-signing-secret-of-at-least-32-chars",
        salt="fastmcp-jwt-signing-key",
    )


def derived_storage_key(jwt_key: bytes) -> bytes:
    # FastMCP's default store key: oauth_proxy/proxy.py derives it from the
    # (already derived) JWT signing key with this salt.
    return derive_jwt_key(
        high_entropy_material=jwt_key.decode(), salt="fastmcp-storage-encryption-key"
    )


@pytest.mark.parametrize(
    "env,expected_key",
    [
        ({"STORAGE_ENCRYPTION_KEY": FERNET_KEY}, FERNET_KEY.encode()),
        (
            {},
            derived_storage_key(
                derive_jwt_key(
                    high_entropy_material="test-client-secret", salt="fastmcp-jwt-signing-key"
                )
            ),
        ),
        (
            {"JWT_SIGNING_KEY": "a-fixed-signing-secret-of-at-least-32-chars"},
            derived_storage_key(
                derive_jwt_key(
                    low_entropy_material="a-fixed-signing-secret-of-at-least-32-chars",
                    salt="fastmcp-jwt-signing-key",
                )
            ),
        ),
    ],
    ids=["explicit-key", "derived-from-client-secret", "derived-from-jwt-signing-key"],
)
def test_redis_store_encryption_key(monkeypatch, env, expected_key):
    # Unset, the key must be derived exactly as FastMCP derives its default
    # store key, so no new key scheme is invented and JWT_SIGNING_KEY alone
    # already decouples storage from CLIENT_SECRET.
    monkeypatch.setenv("CLIENT_SECRET", "test-client-secret")
    with patch("cryptography.fernet.Fernet", wraps=Fernet) as fernet:
        make_provider_kwargs(monkeypatch, REDIS_URL="redis://localhost:6379/1", **env)

    assert fernet.call_args.args[0] == expected_key
