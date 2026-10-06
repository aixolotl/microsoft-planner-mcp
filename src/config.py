from __future__ import annotations

from cryptography.fernet import Fernet
from pydantic import PositiveInt, RedisDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # pydantic-settings reads values from .env first, then falls back to real
    # environment variables. Without env_file every required value must be
    # exported in the shell, which is impractical for local development.
    # Docs: https://docs.pydantic.dev/latest/concepts/pydantic_settings/#dotenv-env-support
    # hide_input_in_errors: REDIS_URL carries the Redis password, and pydantic
    # echoes the raw input in validation errors by default — a malformed URL
    # would print the credential to the startup log.
    # Docs: https://docs.pydantic.dev/latest/api/config/#pydantic.config.ConfigDict.hide_input_in_errors
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", hide_input_in_errors=True
    )

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

    # Sliding-window rate limit applied per client. LLM agents can fan out tool
    # calls rapidly; these caps prevent exhausting the Microsoft Graph throttling
    # quota (10,000 req/10min per tenant). Without configurable limits, operators
    # cannot tune the ceiling to match their tenant's usage patterns.
    # Docs: https://gofastmcp.com/servers/middleware#rate-limiting
    RATE_LIMIT_MAX_REQUESTS: PositiveInt = 120
    RATE_LIMIT_WINDOW_MINUTES: PositiveInt = 1

    # Where the OAuth proxy keeps client registrations and user tokens. Unset,
    # FastMCP uses an encrypted file store inside the container, which is lost
    # on container recreation / scale-to-zero and not shared between replicas
    # — every client then fails token refresh with 401 invalid_client.
    # redis:// or rediss:// (TLS, required by Azure Redis); the path selects
    # the DB index so the store can share a Redis with other services.
    # Docs: https://gofastmcp.com/servers/auth/oauth-proxy
    REDIS_URL: RedisDsn | None = None

    # Fixed secret for signing FastMCP's tokens. Unset, it is derived from
    # CLIENT_SECRET, so rotating the Entra secret logs every user out.
    JWT_SIGNING_KEY: SecretStr | None = None

    # Fernet key encrypting the Redis store. Unset, it is derived the way
    # FastMCP derives its default store key. Only meaningful with REDIS_URL.
    # Docs: https://cryptography.io/en/latest/fernet/
    STORAGE_ENCRYPTION_KEY: SecretStr | None = None

    @field_validator("STORAGE_ENCRYPTION_KEY")
    @classmethod
    def _valid_fernet_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            try:
                Fernet(value.get_secret_value())
            except ValueError as exc:
                raise ValueError(
                    "STORAGE_ENCRYPTION_KEY must be a Fernet key "
                    "(Fernet.generate_key(): 32 url-safe base64-encoded bytes)"
                ) from exc
        return value

    @model_validator(mode="after")
    def _encryption_key_needs_redis(self) -> Settings:
        if self.STORAGE_ENCRYPTION_KEY is not None and self.REDIS_URL is None:
            raise ValueError("STORAGE_ENCRYPTION_KEY requires REDIS_URL")
        return self


# pydantic-settings reads CLIENT_ID, CLIENT_SECRET, and TENANT_ID from the
# environment / .env at runtime, so there are no missing arguments even though
# the static type checker cannot verify environment-derived values itself.
settings = Settings()  # ty:ignore[call-arg]
