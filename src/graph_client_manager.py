from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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

    @asynccontextmanager
    async def for_user(self, obo_token: str) -> AsyncIterator[GraphServiceClient]:
        """Async context manager yielding a GraphServiceClient for the token's user.

        Ensures the underlying OBO credential (and its HTTP transport) are
        closed when the caller exits the ``async with`` block.
        """
        async with OnBehalfOfCredential(
            tenant_id=self._tenant_id,
            client_id=self._client_id,
            client_secret=self._client_secret,
            user_assertion=obo_token,
        ) as credential:
            yield GraphServiceClient(
                credentials=credential,
                scopes=["https://graph.microsoft.com/.default"],
            )


graph_client_manager = GraphClientManager()
