"""Unit tests for configurable rate limiting settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src import config as config_module


# ---------------------------------------------------------------------------
# Tests: rate limiting defaults
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,expected",
    [
        ("RATE_LIMIT_MAX_REQUESTS", 120),
        ("RATE_LIMIT_WINDOW_MINUTES", 1),
    ],
    ids=["max-requests-default", "window-minutes-default"],
)
def test_rate_limit_defaults(monkeypatch, field, expected):
    monkeypatch.delenv(field, raising=False)
    monkeypatch.delenv(field.lower(), raising=False)

    settings = config_module.Settings(_env_file=None)

    assert getattr(settings, field) == expected


# ---------------------------------------------------------------------------
# Tests: rate limiting reads environment variables
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env_var,env_value,field,expected",
    [
        ("RATE_LIMIT_MAX_REQUESTS", "200", "RATE_LIMIT_MAX_REQUESTS", 200),
        ("RATE_LIMIT_WINDOW_MINUTES", "5", "RATE_LIMIT_WINDOW_MINUTES", 5),
        ("rate_limit_max_requests", "60", "RATE_LIMIT_MAX_REQUESTS", 60),
        ("rate_limit_window_minutes", "2", "RATE_LIMIT_WINDOW_MINUTES", 2),
    ],
    ids=[
        "max-requests-uppercase",
        "window-minutes-uppercase",
        "max-requests-lowercase",
        "window-minutes-lowercase",
    ],
)
def test_rate_limit_reads_env(monkeypatch, env_var, env_value, field, expected):
    # Clear both cases to avoid interference
    monkeypatch.delenv(field, raising=False)
    monkeypatch.delenv(field.lower(), raising=False)
    monkeypatch.setenv(env_var, env_value)

    settings = config_module.Settings(_env_file=None)

    assert getattr(settings, field) == expected


# ---------------------------------------------------------------------------
# Tests: rate limiting rejects non-positive values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env_var,env_value",
    [
        ("RATE_LIMIT_MAX_REQUESTS", "0"),
        ("RATE_LIMIT_MAX_REQUESTS", "-1"),
        ("RATE_LIMIT_WINDOW_MINUTES", "0"),
        ("RATE_LIMIT_WINDOW_MINUTES", "-5"),
    ],
    ids=[
        "max-requests-zero",
        "max-requests-negative",
        "window-minutes-zero",
        "window-minutes-negative",
    ],
)
def test_rate_limit_rejects_non_positive(monkeypatch, env_var, env_value):
    monkeypatch.delenv(env_var, raising=False)
    monkeypatch.delenv(env_var.lower(), raising=False)
    monkeypatch.setenv(env_var, env_value)

    with pytest.raises(ValidationError):
        config_module.Settings(_env_file=None)


# ---------------------------------------------------------------------------
# Tests: OAuth proxy state storage settings
# ---------------------------------------------------------------------------

# A valid Fernet key (32 url-safe base64 bytes). Fernet rejects anything else,
# so tests that need a key must use a real one.
# Docs: https://cryptography.io/en/latest/fernet/#cryptography.fernet.Fernet
FERNET_KEY = "fXpQ0Ul6ZJ8fKk5q8D0v0b7n7cK4l9sQ3m2a1b0c9d8="
STORAGE_ENV = ("REDIS_URL", "JWT_SIGNING_KEY", "STORAGE_ENCRYPTION_KEY")


def clear_storage_env(monkeypatch):
    for name in STORAGE_ENV:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


def test_storage_settings_default_to_none(monkeypatch):
    clear_storage_env(monkeypatch)

    settings = config_module.Settings(_env_file=None)

    assert (settings.REDIS_URL, settings.JWT_SIGNING_KEY, settings.STORAGE_ENCRYPTION_KEY) == (
        None,
        None,
        None,
    )


@pytest.mark.parametrize(
    "url",
    ["redis://localhost:6379/1", "rediss://:pw@example.redis.cache.windows.net:6380/0"],
    ids=["redis-plain", "rediss-tls"],
)
def test_redis_url_accepts_redis_and_rediss(monkeypatch, url):
    clear_storage_env(monkeypatch)
    monkeypatch.setenv("REDIS_URL", url)

    settings = config_module.Settings(_env_file=None)

    assert str(settings.REDIS_URL) == url


@pytest.mark.parametrize(
    "url",
    ["http://:s3cret-pw@redis:6379", "s3cret-pw@redis:6379"],
    ids=["wrong-scheme", "no-scheme"],
)
def test_invalid_redis_url_fails_without_leaking_it(monkeypatch, url):
    # REDIS_URL carries the Redis password. pydantic echoes input_value in
    # errors by default, which would print the credential to startup logs.
    clear_storage_env(monkeypatch)
    monkeypatch.setenv("REDIS_URL", url)

    with pytest.raises(ValidationError) as exc:
        config_module.Settings(_env_file=None)

    assert "REDIS_URL" in str(exc.value)
    assert "s3cret-pw" not in str(exc.value)


def test_storage_encryption_key_without_redis_url_fails(monkeypatch):
    # The key only ever applies to the Redis store; set alone it would be a
    # silent no-op, so startup refuses it.
    clear_storage_env(monkeypatch)
    monkeypatch.setenv("STORAGE_ENCRYPTION_KEY", FERNET_KEY)

    with pytest.raises(ValidationError, match="STORAGE_ENCRYPTION_KEY requires REDIS_URL"):
        config_module.Settings(_env_file=None)


def test_invalid_storage_encryption_key_fails(monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/1")
    monkeypatch.setenv("STORAGE_ENCRYPTION_KEY", "not-a-fernet-key")

    with pytest.raises(ValidationError, match="STORAGE_ENCRYPTION_KEY"):
        config_module.Settings(_env_file=None)


@pytest.mark.parametrize("name", STORAGE_ENV, ids=["redis-url", "jwt-key", "encryption-key"])
def test_empty_storage_setting_means_unset(monkeypatch, name):
    # `JWT_SIGNING_KEY=` (an uncommented .env.example line, or an unset
    # compose ${VAR}) must not become SecretStr(''): an empty signing key
    # would derive a publicly computable key for the Redis store.
    clear_storage_env(monkeypatch)
    monkeypatch.setenv(name, "")

    settings = config_module.Settings(_env_file=None)

    assert getattr(settings, name) is None


def test_short_jwt_signing_key_fails(monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.setenv("JWT_SIGNING_KEY", "too-short")

    with pytest.raises(ValidationError, match="JWT_SIGNING_KEY"):
        config_module.Settings(_env_file=None)


@pytest.mark.parametrize(
    "url",
    ["redis://localhost:6379/abc", "redis://localhost:6379/1/extra"],
    ids=["non-numeric-db", "nested-path"],
)
def test_redis_url_with_invalid_db_path_fails(monkeypatch, url):
    # redis-py ignores a non-numeric path and silently uses DB 0 — the DB
    # another service on a shared Redis is most likely to be using.
    clear_storage_env(monkeypatch)
    monkeypatch.setenv("REDIS_URL", url)

    with pytest.raises(ValidationError, match="REDIS_URL"):
        config_module.Settings(_env_file=None)


def test_settings_repr_hides_redis_password(monkeypatch):
    clear_storage_env(monkeypatch)
    monkeypatch.setenv("REDIS_URL", "rediss://:s3cret-pw@example.redis.cache.windows.net:6380/0")

    settings = config_module.Settings(_env_file=None)

    assert "s3cret-pw" not in repr(settings)
