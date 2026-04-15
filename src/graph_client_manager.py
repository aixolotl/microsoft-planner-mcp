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
        # Singleton: the manager holds only Azure app credentials (tenant/client
        # id/secret), which are shared across all users. The per-user OBO
        # credential is created fresh inside for_user() on every call, so
        # there is no shared mutable state between requests.
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
        # OnBehalfOfCredential performs the OAuth 2.0 On-Behalf-Of (OBO) flow:
        # it exchanges the MCP session token (issued to the client by Entra ID)
        # for a new access token scoped to Microsoft Graph, acting on behalf of
        # the authenticated user.  This means Graph calls run as the user, not
        # the application, so Planner enforces their own plan/task permissions.
        # Docs: https://learn.microsoft.com/en-us/azure/active-directory/develop/v2-oauth2-on-behalf-of-flow
        credential = OnBehalfOfCredential(
            tenant_id=self._tenant_id,
            client_id=self._client_id,
            client_secret=self._client_secret,
            user_assertion=obo_token,
        )
        try:
            yield GraphServiceClient(
                credentials=credential,
                # .default tells Entra ID to issue a token with all previously
                # consented Graph scopes (Tasks.ReadWrite, User.Read). Without
                # this the SDK would need to enumerate scopes explicitly, and
                # any scope not listed would be silently dropped from the token.
                scopes=["https://graph.microsoft.com/.default"],
            )
        except Exception:
            logger.exception("Failed during OBO Graph request")
            raise
        finally:
            # Azure SDK credentials hold an internal MSAL token cache and an
            # aiohttp session. Closing releases those resources; without this
            # the process leaks file descriptors and memory under load.
            await credential.close()


graph_client_manager = GraphClientManager()
