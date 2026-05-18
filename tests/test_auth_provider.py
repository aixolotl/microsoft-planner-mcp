"""Unit tests for auth provider configuration."""

from __future__ import annotations

import importlib
import sys
from unittest.mock import patch

from src import config as config_module

MODULE = "src.auth_provider"
# MODULE is reloaded after patching because AzureProvider is constructed at
# import time. Without re-importing the usage module, the test would inspect an
# already-created provider and miss whether new settings are forwarded.
# Docs: https://docs.python.org/3/library/importlib.html#importlib.reload


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
    monkeypatch.setenv("require_authorization_consent", "false")

    settings = config_module.Settings(_env_file=None)
    original_settings = config_module.settings
    existing_module = sys.modules.get(MODULE)

    try:
        with patch.object(config_module, "settings", settings), patch(
            "fastmcp.server.auth.providers.azure.AzureProvider"
        ) as mock_provider:
            if existing_module is None:
                importlib.import_module(MODULE)
            else:
                importlib.reload(existing_module)

        mock_provider.assert_called_once()
        assert mock_provider.call_args.kwargs["require_authorization_consent"] is False
    finally:
        config_module.settings = original_settings
        if MODULE in sys.modules:
            importlib.reload(sys.modules[MODULE])
