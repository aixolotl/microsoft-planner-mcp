from __future__ import annotations

# AzureProvider implements an OAuth 2.0 authorization server backed by
# Microsoft Entra ID. It handles the full PKCE authorization code flow:
#   browser → /auth/authorize → Entra ID → /auth/callback → MCP session token
# The resulting session token is validated by FastMCP on every tool call, so
# no tool executes without a verified Entra ID identity.
# Docs: https://gofastmcp.com/servers/auth/authentication
from cryptography.fernet import Fernet
from fastmcp.server.auth.jwt_issuer import derive_jwt_key
from fastmcp.server.auth.providers.azure import AzureProvider
from key_value.aio.stores.redis import RedisStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from redis.asyncio import Redis

from .config import settings


def _redis_client_storage() -> FernetEncryptionWrapper | None:
    """Encrypted Redis store for OAuth proxy state, or None for FastMCP's default.

    The default is a file store inside the container: container recreation,
    scale-to-zero or a second replica loses every client registration and
    token, and refreshes fail with 401 invalid_client. The store holds users'
    Entra refresh tokens, so it is always Fernet-encrypted.
    Docs: https://gofastmcp.com/servers/auth/oauth-proxy
    """
    if settings.REDIS_URL is None:
        return None

    if settings.STORAGE_ENCRYPTION_KEY is not None:
        key = settings.STORAGE_ENCRYPTION_KEY.get_secret_value().encode()
    else:
        # Mirror FastMCP's default-store key derivation (oauth_proxy/proxy.py):
        # storage key from the JWT signing key, itself from JWT_SIGNING_KEY or
        # CLIENT_SECRET. Without this, no key scheme would match FastMCP's.
        if settings.JWT_SIGNING_KEY is not None:
            jwt_key = derive_jwt_key(
                low_entropy_material=settings.JWT_SIGNING_KEY.get_secret_value(),
                salt="fastmcp-jwt-signing-key",
            )
        else:
            jwt_key = derive_jwt_key(
                high_entropy_material=settings.CLIENT_SECRET, salt="fastmcp-jwt-signing-key"
            )
        key = derive_jwt_key(
            high_entropy_material=jwt_key.decode(), salt="fastmcp-storage-encryption-key"
        )

    return FernetEncryptionWrapper(
        # RedisStore(url=...) rebuilds the client from host/port/db/password and
        # drops TLS, so a rediss:// URL (Azure Redis) would connect in plaintext
        # and fail. redis-py's from_url honours the scheme.
        key_value=RedisStore(client=Redis.from_url(str(settings.REDIS_URL), decode_responses=True)),
        fernet=Fernet(key),
        # A rotated key turns old entries into cache misses (users reconnect)
        # instead of crashing the token endpoint — FastMCP's default behaviour.
        raise_on_decryption_error=False,
    )


auth = AzureProvider(
    client_id=settings.CLIENT_ID,
    client_secret=settings.CLIENT_SECRET,
    tenant_id=settings.TENANT_ID,
    base_url=settings.BASE_URL,
    # The MCP-level scope the client must request and that must be present in
    # the incoming token. AzureProvider rejects any call whose token does not
    # carry this scope. Without it, any valid Entra ID token — even one issued
    # for a different application entirely — would be accepted.
    required_scopes=["mcp-access"],
    # Graph scopes added to the Entra ID /authorize request so the user
    # grants consent to Planner and profile access during the same OAuth
    # exchange. Without these, the access token passed to the OBO flow in
    # GraphClientManager.for_user() will not carry Tasks.ReadWrite / User.Read
    # consent and every Graph call will fail with AADSTS65001.
    additional_authorize_scopes=[
        "https://graph.microsoft.com/Tasks.ReadWrite",
        "https://graph.microsoft.com/User.Read",
        # Required by the list_users tool to resolve user display names from the
        # GUIDs that appear in task assignment objects. Without this scope, Graph
        # returns 403 Forbidden for /users and /users?$filter=id eq '...' calls.
        # Docs: https://learn.microsoft.com/en-us/graph/permissions-reference#userreadbasicall
        "https://graph.microsoft.com/User.ReadBasic.All",
    ],
    # FastMCP defaults this to True to force explicit client approval.
    # Without a config escape hatch, local development with disposable clients
    # must repeat that consent flow even when the operator intentionally wants
    # to disable it. Docs:
    # https://gofastmcp.com/servers/auth/oauth-proxy#param-require-authorization-consent
    require_authorization_consent=settings.REQUIRE_AUTHORIZATION_CONSENT,
    # Both None keeps FastMCP's defaults (file store, key from CLIENT_SECRET).
    client_storage=_redis_client_storage(),
    jwt_signing_key=(
        settings.JWT_SIGNING_KEY.get_secret_value() if settings.JWT_SIGNING_KEY else None
    ),
)
