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
