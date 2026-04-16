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

import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, TypeVar

from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.headers_collection import HeadersCollection
from kiota_serialization_json.json_serialization_writer import JsonSerializationWriter
from msgraph import GraphServiceClient
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_task import PlannerTask
from msgraph.generated.models.planner_task_details import PlannerTaskDetails
from msgraph_core.tasks.page_iterator import PageIterator

from ..types import CollectionRequestBuilder

T = TypeVar("T")


class PlannerService:
    """Planner operations over a caller-supplied GraphServiceClient."""

    def __init__(self, client: GraphServiceClient, *, serialize: bool = True) -> None:
        self._client = client
        # When True, return values from tools are converted from Kiota Parsable
        # objects to plain dicts via JSON round-trip. Without serialization,
        # FastMCP's default serializer produces incomplete or opaque output
        # because it does not understand Kiota models.
        self.serialize = serialize

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    def serialize_graph_object(self, obj: Any) -> Any:
        """Convert a Kiota Parsable object to a plain dict via JSON round-trip.

        Kiota model objects (PlannerTask, Group, User, etc.) implement
        Parsable.serialize() which writes fields into a SerializationWriter.
        Using JsonSerializationWriter produces a UTF-8 JSON byte string that
        we decode and parse into a native dict. Without this, returning a raw
        Kiota object from an MCP tool can produce incomplete or opaque output
        because FastMCP's default serializer does not understand Kiota models.

        When self.serialize is False the original object is returned unchanged —
        useful for internal callers that need the typed SDK object.
        """
        if not self.serialize:
            return obj
        writer = JsonSerializationWriter()
        obj.serialize(writer)
        return json.loads(writer.get_serialized_content().decode("utf-8"))

    def serialize_graph_list(self, items: list[Any]) -> list[Any]:
        """Convert a list of Kiota Parsable objects to a list of plain dicts.

        Each item is serialized independently via serialize_graph_object. When
        self.serialize is False the original list is returned unchanged.
        """
        if not self.serialize:
            return items
        return [self.serialize_graph_object(item) for item in items]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def paginate(request_builder: CollectionRequestBuilder, request_configuration: RequestConfiguration | None = None) -> list:
        """Fetch all pages from a Graph collection endpoint using PageIterator.

        Args:
            request_builder: A Graph SDK request builder that supports .get().
            request_configuration: Optional RequestConfiguration for the first request.

        Returns:
            Flat list of all items from all pages.
        """
        result = await request_builder.get(request_configuration=request_configuration)
        if result is None or not result.value:
            return []
        all_items: list = []
        # PageIterator follows @odata.nextLink URLs automatically until no more
        # pages exist. Using it instead of a manual while-loop is safer because
        # the SDK handles auth header injection, request retry, and deseriali-
        # sation for each subsequent page request.
        # Docs: https://learn.microsoft.com/en-us/graph/sdks/paging
        page_iterator = PageIterator(result, request_builder.request_adapter)
        await page_iterator.iterate(lambda item: all_items.append(item) or True)
        return all_items

    @staticmethod
    def to_utc(s: str) -> datetime:
        """Parse an ISO 8601 datetime string and return it normalised to UTC.

        Naive datetimes (no timezone) are assumed to be UTC.
        """
        dt = datetime.fromisoformat(s)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)

    @staticmethod
    def _make_config(etag: str, *, prefer_representation: bool = False) -> RequestConfiguration:
        """Build a RequestConfiguration with If-Match and optionally Prefer: return=representation.

        HeadersCollection must be passed explicitly — RequestConfiguration uses
        a shared mutable default for `headers` that would bleed across calls.
        """
        # A fresh HeadersCollection must be created for every config object.
        # RequestConfiguration's default `headers` field is a class-level
        # mutable default shared across all instances; re-using it causes
        # If-Match values from one call to bleed into the next. This is covered
        # by test_configs_do_not_share_headers in test_planner_service.py.
        headers = HeadersCollection()
        headers.add("If-Match", etag)
        if prefer_representation:
            # "Prefer: return=representation" tells Graph to return the updated
            # object in the PATCH response body instead of a 204 No Content.
            # Without it, PATCH returns nothing and callers cannot confirm the
            # new field values without an extra GET request.
            # Docs: https://learn.microsoft.com/en-us/graph/api/plannertask-update
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

    async def _refresh_plan_etag(self, plan_id: str) -> str:
        """GET the plan and return the current @odata.etag value."""
        plan = await self._client.planner.plans.by_planner_plan_id(plan_id).get()
        etag: str | None = plan.additional_data.get("@odata.etag") if plan else None
        if not etag:
            raise ValueError(f"No @odata.etag found on plan {plan_id!r}")
        return etag

    async def _refresh_bucket_etag(self, bucket_id: str) -> str:
        """GET the bucket and return the current @odata.etag value."""
        bucket = await self._client.planner.buckets.by_planner_bucket_id(bucket_id).get()
        etag: str | None = bucket.additional_data.get("@odata.etag") if bucket else None
        if not etag:
            raise ValueError(f"No @odata.etag found on bucket {bucket_id!r}")
        return etag

    async def _with_retry(self, etag: str, operation: Callable[[str], Awaitable[T]], refresh: Callable[[], Awaitable[str]]) -> T:
        """Run ``operation(etag)``, retrying once with a fresh ETag on 412/409."""
        try:
            return await operation(etag)
        except ODataError as exc:
            # Graph Planner requires an If-Match header containing the current
            # ETag of the resource. If another client has modified the resource
            # since the caller last read it, Graph returns 412 (Precondition
            # Failed) or 409 (Conflict). Rather than surfacing this as an error,
            # we re-fetch the current ETag and retry once. A single retry is
            # sufficient because concurrent modifications are rare and a second
            # conflict is indistinguishable from a persistent server error.
            # Docs: https://learn.microsoft.com/en-us/graph/api/plannertask-update#request-headers
            if exc.response_status_code not in (409, 412):
                raise
            return await operation(await refresh())

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
            etag,
            lambda e: item.patch(body, request_configuration=self._make_config(e, prefer_representation=True)),
            lambda: self._refresh_task_etag(task_id),
        )

    async def delete_task(self, task_id: str, etag: str) -> None:
        """DELETE a task.  Retries once with a fresh ETag on 412/409."""
        item = self._client.planner.tasks.by_planner_task_id(task_id)
        await self._with_retry(
            etag,
            lambda e: item.delete(request_configuration=self._make_config(e)),
            lambda: self._refresh_task_etag(task_id),
        )

    async def patch_task_details(
        self,
        task_id: str,
        body: PlannerTaskDetails,
        etag: str,
    ) -> PlannerTaskDetails | None:
        """PATCH task details.  Retries once with a fresh ETag on 412/409."""
        item = self._client.planner.tasks.by_planner_task_id(task_id).details
        return await self._with_retry(
            etag,
            lambda e: item.patch(body, request_configuration=self._make_config(e, prefer_representation=True)),
            lambda: self._refresh_details_etag(task_id),
        )

    async def delete_plan(self, plan_id: str, etag: str) -> None:
        """DELETE a plan.  Retries once with a fresh ETag on 412/409."""
        item = self._client.planner.plans.by_planner_plan_id(plan_id)
        await self._with_retry(
            etag,
            lambda e: item.delete(request_configuration=self._make_config(e)),
            lambda: self._refresh_plan_etag(plan_id),
        )

    async def delete_bucket(self, bucket_id: str, etag: str) -> None:
        """DELETE a bucket.  Retries once with a fresh ETag on 412/409."""
        item = self._client.planner.buckets.by_planner_bucket_id(bucket_id)
        await self._with_retry(
            etag,
            lambda e: item.delete(request_configuration=self._make_config(e)),
            lambda: self._refresh_bucket_etag(bucket_id),
        )
