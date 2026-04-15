"""
PlannerService — wraps GraphServiceClient for Planner PATCH/DELETE operations.

Uses SDK primitives only:
- RequestConfiguration (kiota_abstractions) for If-Match / Prefer headers.
- ODataError (msgraph) for structured error access.
- Read-Resolve-Retry on 412/409: re-GETs the task for a fresh ETag, retries once.

All non-retryable errors surface as ODataError directly; callers can inspect
.error.code and .error.message for details.  If the retry path cannot resolve
a fresh ETag (task missing or response contains no @odata.etag), a ValueError
is raised from _refresh_task_etag.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.headers_collection import HeadersCollection
from msgraph import GraphServiceClient
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_task import PlannerTask
from msgraph.generated.models.planner_task_details import PlannerTaskDetails

T = TypeVar("T")


class PlannerService:
    """Planner operations over a caller-supplied GraphServiceClient."""

    def __init__(self, client: GraphServiceClient) -> None:
        self._client = client

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_config(etag: str, *, prefer_representation: bool = False) -> RequestConfiguration:
        """Build a RequestConfiguration with If-Match and optionally Prefer: return=representation.

        HeadersCollection must be passed explicitly — RequestConfiguration uses
        a shared mutable default for `headers` that would bleed across calls.
        """
        headers = HeadersCollection()
        headers.add("If-Match", etag)
        if prefer_representation:
            headers.add("Prefer", "return=representation")
        return RequestConfiguration(headers=headers)

    async def _refresh_task_etag(self, task_id: str) -> str:
        """GET the task and return the current @odata.etag value."""
        task = await self._client.planner.tasks.by_planner_task_id(task_id).get()
        etag: str | None = task.additional_data.get("@odata.etag") if task else None
        if not etag:
            raise ValueError(f"No @odata.etag found on task {task_id!r}")
        return etag

    async def _refresh_details_etag(self, task_id: str) -> str:
        """GET the task details and return the current @odata.etag value."""
        details = await self._client.planner.tasks.by_planner_task_id(task_id).details.get()
        etag: str | None = details.additional_data.get("@odata.etag") if details else None
        if not etag:
            raise ValueError(f"No @odata.etag found on task details {task_id!r}")
        return etag

    async def _with_retry(self, task_id: str, etag: str, operation: Callable[[str], Awaitable[T]]) -> T:
        """Run ``operation(etag)``, retrying once with a fresh ETag on 412/409."""
        try:
            return await operation(etag)
        except ODataError as exc:
            if exc.response_status_code not in (409, 412):
                raise
            return await operation(await self._refresh_task_etag(task_id))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def patch_task(
        self,
        task_id: str,
        body: PlannerTask,
        etag: str,
    ) -> PlannerTask | None:
        """PATCH a task.  Retries once with a fresh ETag on 412/409."""
        item = self._client.planner.tasks.by_planner_task_id(task_id)
        return await self._with_retry(
            task_id, etag,
            lambda e: item.patch(body, request_configuration=self._make_config(e, prefer_representation=True)),
        )

    async def delete_task(self, task_id: str, etag: str) -> None:
        """DELETE a task.  Retries once with a fresh ETag on 412/409."""
        item = self._client.planner.tasks.by_planner_task_id(task_id)
        await self._with_retry(
            task_id, etag,
            lambda e: item.delete(request_configuration=self._make_config(e)),
        )

    async def patch_task_details(
        self,
        task_id: str,
        body: PlannerTaskDetails,
        etag: str,
    ) -> PlannerTaskDetails | None:
        """PATCH task details.  Retries once with a fresh ETag on 412/409."""
        item = self._client.planner.tasks.by_planner_task_id(task_id).details
        try:
            return await item.patch(body, request_configuration=self._make_config(etag, prefer_representation=True))
        except ODataError as exc:
            if exc.response_status_code not in (409, 412):
                raise
            fresh_etag = await self._refresh_details_etag(task_id)
            return await item.patch(body, request_configuration=self._make_config(fresh_etag, prefer_representation=True))
