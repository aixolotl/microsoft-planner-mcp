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
    ],
)
