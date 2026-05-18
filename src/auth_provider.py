from __future__ import annotations

# AzureProvider implements an OAuth 2.0 authorization server backed by
# Microsoft Entra ID. It handles the full PKCE authorization code flow:
#   browser → /auth/authorize → Entra ID → /auth/callback → MCP session token
# The resulting session token is validated by FastMCP on every tool call, so
# no tool executes without a verified Entra ID identity.
# Docs: https://gofastmcp.com/servers/auth/authentication
from fastmcp.server.auth.providers.azure import AzureProvider

from .config import settings

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
)
