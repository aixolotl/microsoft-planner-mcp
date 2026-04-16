"""BasePlannerService — shared infrastructure for all Planner service classes."""

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


class BasePlannerService:
    """Shared infrastructure for all Planner service classes."""

    def __init__(self, client: GraphServiceClient, *, serialize: bool = False) -> None:
        self._client = client
        # serialize=True makes paginate() and mutation methods return plain dicts
        # instead of typed SDK objects, so tool routers can return the result
        # directly without a separate serialize_graph_* call at the call site.
        self._serialize = serialize

    async def paginate(self, request_builder: CollectionRequestBuilder, request_configuration: RequestConfiguration | None = None) -> list:
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
        # When serialize=True, convert SDK objects to plain dicts so the caller
        # can return the list directly without a separate serialize_graph_list()
        # call. When False, raw SDK objects are returned for tests that assert
        # on object identity or typed attributes.
        return self.serialize_graph_list(all_items) if self._serialize else all_items

    @staticmethod
    def serialize_graph_object(obj: Any) -> dict | None:
        """Serialize a Kiota Graph SDK object to a clean dict using Kiota's own serializer.

        JsonSerializationWriter emits only non-null fields and merges additional_data
        entries (e.g. @odata.etag) inline at the top level. Without this, FastMCP
        falls back to pydantic_core.to_json with vars(obj), which stringifies
        backing_store and dumps every null-default field.
        Docs: https://github.com/microsoft/kiota-python/tree/main/packages/serialization/json
        """
        if obj is None:
            return None
        writer = JsonSerializationWriter()
        obj.serialize(writer)
        return json.loads(writer.get_serialized_content())

    @staticmethod
    def serialize_graph_list(items: list[Any]) -> list[dict]:
        """Serialize a list of Kiota Graph SDK objects to a list of clean dicts."""
        return [
            serialized
            for item in items
            if item is not None
            if (serialized := BasePlannerService.serialize_graph_object(item)) is not None
        ]

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
        # by test_configs_do_not_share_headers in test_task_service.py.
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
