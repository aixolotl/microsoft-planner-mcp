from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # pydantic-settings reads values from .env first, then falls back to real
    # environment variables. Without env_file every required value must be
    # exported in the shell, which is impractical for local development.
    # Docs: https://docs.pydantic.dev/latest/concepts/pydantic_settings/#dotenv-env-support
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Azure Entra ID app registration credentials. Used by AzureProvider
    # (auth_provider.py) to drive the OAuth authorization code flow and by
    # GraphClientManager (graph_client_manager.py) for the On-Behalf-Of (OBO)
    # token exchange. If any value is wrong or missing the server starts but
    # every OAuth redirect and every Graph API call will fail immediately.
    CLIENT_ID: str
    CLIENT_SECRET: str
    TENANT_ID: str

    # Public-facing base URL of this server. AzureProvider appends
    # "/auth/callback" to build the OAuth redirect_uri sent to Entra ID.
    # Must match exactly what is registered under Redirect URIs in the Azure
    # App Registration — a mismatch causes Entra ID to return AADSTS50011.
    BASE_URL: str = "http://localhost:8000"

    # Origins permitted to make cross-origin requests. Used by CORSMiddleware
    # in server.py. Must include the connecting MCP client's origin (e.g.
    # MCP Inspector at http://localhost:6274). Without a matching entry,
    # browsers block the CORS preflight and the client cannot connect.
    # Docs: https://developer.mozilla.org/en-US/docs/Web/HTTP/CORS
    ALLOWED_ORIGINS: list[str] = ["http://localhost:8000"]

    # FastMCP requires per-client auth consent by default to prevent confused
    # deputy attacks. Setting this false helps local development with throwaway
    # clients but removes that extra approval step. Docs:
    # https://gofastmcp.com/servers/auth/oauth-proxy#param-require-authorization-consent
    REQUIRE_AUTHORIZATION_CONSENT: bool = True


# pydantic-settings reads CLIENT_ID, CLIENT_SECRET, and TENANT_ID from the
# environment / .env at runtime, so there are no missing arguments even though
# the static type checker cannot verify environment-derived values itself.
settings = Settings()  # ty:ignore[missing-argument]
