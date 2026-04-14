import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from azure.identity.aio import OnBehalfOfCredential
from msgraph import GraphServiceClient

from .config import settings

logger = logging.getLogger(__name__)


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
            logger.info(
                "GraphClientManager initialised",
                extra={"tenant_id": self._tenant_id, "client_id": self._client_id},
            )

    @asynccontextmanager
    async def for_user(self, obo_token: str) -> AsyncGenerator[GraphServiceClient, None]:
        """Async context manager yielding a GraphServiceClient for the token's user."""
        logger.debug("Creating OBO credential for user token")
        credential = OnBehalfOfCredential(
            tenant_id=self._tenant_id,
            client_id=self._client_id,
            client_secret=self._client_secret,
            user_assertion=obo_token,
        )
        try:
            yield GraphServiceClient(
                credentials=credential,
                scopes=["https://graph.microsoft.com/.default"],
            )
        except Exception:
            logger.exception("Failed during OBO Graph request")
            raise
        finally:
            await credential.close()


graph_client_manager = GraphClientManager()
