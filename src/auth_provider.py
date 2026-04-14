from fastmcp.server.auth.providers.azure import AzureProvider

from .config import settings

auth = AzureProvider(
    client_id=settings.CLIENT_ID,
    client_secret=settings.CLIENT_SECRET,
    tenant_id=settings.TENANT_ID,
    base_url=settings.BASE_URL,
    required_scopes=["mcp-access"],
    additional_authorize_scopes=[
        "https://graph.microsoft.com/Tasks.ReadWrite",
        "https://graph.microsoft.com/User.Read",
        "https://graph.microsoft.com/Group.Read.All",
    ],
)
