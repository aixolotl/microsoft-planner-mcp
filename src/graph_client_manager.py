from azure.identity.aio import OnBehalfOfCredential
from msgraph import GraphServiceClient

from .config import settings


class GraphClientManager:
    """Factory for creating per-request GraphServiceClient instances via OBO."""

    _instance: "GraphClientManager | None" = None

    def __new__(cls) -> "GraphClientManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not hasattr(self, "_initialised"):
            self._tenant_id = settings.TENANT_ID
            self._client_id = settings.CLIENT_ID
            self._client_secret = settings.CLIENT_SECRET
            self._initialised = True

    def for_user(self, obo_token: str) -> GraphServiceClient:
        """Return a GraphServiceClient that acts on behalf of the token's user."""
        credential = OnBehalfOfCredential(
            tenant_id=self._tenant_id,
            client_id=self._client_id,
            client_secret=self._client_secret,
            user_assertion=obo_token,
        )
        return GraphServiceClient(
            credentials=credential,
            scopes=["https://graph.microsoft.com/.default"],
        )


graph_client_manager = GraphClientManager()
