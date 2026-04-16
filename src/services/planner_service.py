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
        # Docs: https://learn.microsoft.com/en-us/openapi/kiota/serialization
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
    # Request helpers
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
    def make_config(etag: str, *, prefer_representation: bool = False) -> RequestConfiguration:
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

    @staticmethod
    async def refresh_etag(get_fn: Callable[[], Awaitable[Any]], label: str) -> str:
        """GET a resource and return its current @odata.etag value.

        Consolidates the per-resource refresh helpers into a single function.
        Without a current ETag, Graph rejects PATCH/DELETE with 428 Precondition
        Required. The label is used only in the ValueError message on failure.

        Accepts a zero-arg callable that returns an awaitable (e.g. ``item.get``)
        rather than a pre-built coroutine. This ensures a fresh coroutine is
        created on each invocation — awaiting the same coroutine twice raises
        ``RuntimeError: cannot reuse already awaited coroutine``.
        """
        obj = await get_fn()
        etag: str | None = obj.additional_data.get("@odata.etag") if obj else None
        if not etag:
            raise ValueError(f"No @odata.etag found on {label}")
        return etag

    async def with_retry(self, etag: str, operation: Callable[[str], Awaitable[T]], refresh: Callable[[], Awaitable[str]]) -> T:
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
