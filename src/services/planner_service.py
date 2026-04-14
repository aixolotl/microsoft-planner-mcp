"""
PlannerService — wraps GraphServiceClient for Planner PATCH/DELETE operations.

Uses SDK primitives only:
- RequestConfiguration (kiota_abstractions) for If-Match / Prefer headers.
- ODataError (msgraph) for structured error access.
- Read-Resolve-Retry on 412/409: re-GETs the task for a fresh ETag, retries once.

All non-retryable errors surface as ODataError directly; callers can inspect
.error.code and .error.message for details.
"""

from __future__ import annotations

from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.headers_collection import HeadersCollection
from msgraph import GraphServiceClient
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_task import PlannerTask


class PlannerService:
    """Planner operations over a caller-supplied GraphServiceClient."""

    def __init__(self, client: GraphServiceClient) -> None:
        self._client = client

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _patch_config(etag: str) -> RequestConfiguration:
        """RequestConfiguration with If-Match and Prefer: return=representation.

        HeadersCollection must be passed explicitly — RequestConfiguration uses
        a shared mutable default for `headers` that would bleed across calls.
        """
        headers = HeadersCollection()
        headers.add("If-Match", etag)
        headers.add("Prefer", "return=representation")
        return RequestConfiguration(headers=headers)

    @staticmethod
    def _delete_config(etag: str) -> RequestConfiguration:
        """RequestConfiguration with only the If-Match header."""
        headers = HeadersCollection()
        headers.add("If-Match", etag)
        return RequestConfiguration(headers=headers)

    async def _refresh_task_etag(self, task_id: str) -> str:
        """GET the task and return the current @odata.etag value."""
        task = await self._client.planner.tasks.by_planner_task_id(task_id).get()
        etag: str | None = task.additional_data.get("@odata.etag") if task else None
        if not etag:
            raise ValueError(f"No @odata.etag found on task {task_id!r}")
        return etag

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def patch_task(
        self,
        task_id: str,
        body: PlannerTask,
        etag: str,
    ) -> PlannerTask | None:
        """PATCH a task.  Retries once with a fresh ETag on 412/409.

        Returns the updated PlannerTask when the server honours
        ``Prefer: return=representation``, otherwise ``None``.
        All other ODataErrors are re-raised as-is.
        """
        try:
            return await self._client.planner.tasks.by_planner_task_id(
                task_id
            ).patch(body, request_configuration=self._patch_config(etag))
        except ODataError as exc:
            if exc.response_status_code not in (409, 412):
                raise
            fresh_etag = await self._refresh_task_etag(task_id)
            return await self._client.planner.tasks.by_planner_task_id(
                task_id
            ).patch(body, request_configuration=self._patch_config(fresh_etag))

    async def delete_task(self, task_id: str, etag: str) -> None:
        """DELETE a task.  Retries once with a fresh ETag on 412/409.

        All other ODataErrors are re-raised as-is.
        """
        try:
            await self._client.planner.tasks.by_planner_task_id(
                task_id
            ).delete(request_configuration=self._delete_config(etag))
        except ODataError as exc:
            if exc.response_status_code not in (409, 412):
                raise
            fresh_etag = await self._refresh_task_etag(task_id)
            await self._client.planner.tasks.by_planner_task_id(
                task_id
            ).delete(request_configuration=self._delete_config(fresh_etag))
