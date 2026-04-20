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
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, TypeVar

from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.headers_collection import HeadersCollection
from kiota_serialization_json.json_serialization_writer import JsonSerializationWriter
from msgraph import GraphServiceClient
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph_core.tasks.page_iterator import PageIterator
from odata_v4_query import ODataFilterParser
from odata_v4_query.filter_parser import FilterNode

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

    # ------------------------------------------------------------------
    # Client-side OData filter / search
    # ------------------------------------------------------------------
    # The Graph Planner API ignores $filter and $search on
    # /me/planner/tasks and /planner/plans/{id}/tasks, so we fetch all
    # items via paginate() above and apply filtering in Python.
    # Parsing is delegated to odata-v4-query; evaluation walks the AST
    # against object attributes.
    # Docs (Graph limitation): https://github.com/aixolotl/microsoft-planner-mcp/issues/29

    # OData uses camelCase field names; Kiota models use snake_case.
    # This regex converts camelCase → snake_case so getattr() works on
    # raw SDK objects. Without it, ``filter=percentComplete eq 50``
    # would fail because PlannerTask uses ``percent_complete``.
    _CAMEL_RE = re.compile(r"(?<=[a-z0-9])([A-Z])")

    @classmethod
    def _to_snake(cls, name: str) -> str:
        return cls._CAMEL_RE.sub(r"_\1", name).lower()

    _filter_parser = ODataFilterParser()

    @classmethod
    def _get_attr(cls, item: Any, field_name: str) -> Any:
        """Resolve a field on a Kiota model object using snake_case conversion.

        OData filter expressions use camelCase (``percentComplete``), but Kiota
        SDK models expose attributes in snake_case (``percent_complete``).
        This helper converts and falls back to the original name so both forms
        work. Returns ``None`` when the attribute does not exist.
        """
        snake = cls._to_snake(field_name)
        val = getattr(item, snake, None)
        if val is None and snake != field_name:
            val = getattr(item, field_name, None)
        return val

    @classmethod
    def _eval_node(cls, node: FilterNode, item: Any) -> Any:
        """Walk a FilterNode AST and evaluate it against a single item.

        Returns a bool for logical/comparison nodes, or a Python value for
        literal/identifier/function nodes used as sub-expressions.

        Raises ValueError for unsupported node types or operators.
        """
        if node.type_ == "literal":
            return node.value

        if node.type_ == "identifier":
            if node.value is None:
                return None
            # OData boolean keywords ``true``, ``false``, and ``null`` are
            # parsed as identifiers by odata-v4-query, not as literals.
            # Without this check, ``hasDescription eq true`` would attempt
            # getattr(item, "true") and always return None → mismatch.
            lower = node.value.lower()
            if lower == "true":
                return True
            if lower == "false":
                return False
            if lower == "null":
                return None
            return cls._get_attr(item, node.value)

        if node.type_ == "function":
            return cls._eval_function(node, item)

        if node.type_ == "operator":
            return cls._eval_operator(node, item)

        raise ValueError(f"Unsupported filter node type: {node.type_!r}")

    @classmethod
    def _eval_function(cls, node: FilterNode, item: Any) -> bool:
        """Evaluate a function call node (startswith, endswith, contains)."""
        fn_name = (node.value or "").lower()
        args = node.arguments or []
        if fn_name in ("startswith", "endswith", "contains") and len(args) == 2:
            field_val = cls._eval_node(args[0], item)
            literal_val = cls._eval_node(args[1], item)
            if field_val is None or literal_val is None:
                return False
            field_str = str(field_val).lower()
            literal_str = str(literal_val).lower()
            if fn_name == "startswith":
                return field_str.startswith(literal_str)
            if fn_name == "endswith":
                return field_str.endswith(literal_str)
            return literal_str in field_str  # contains
        raise ValueError(
            f"Unsupported filter function: {fn_name}({len(args)} args). "
            "Supported: startswith(field, value), endswith(field, value), "
            "contains(field, value)."
        )

    @classmethod
    def _eval_operator(cls, node: FilterNode, item: Any) -> bool:
        """Evaluate a comparison or logical operator node."""
        op = (node.value or "").lower()

        # Logical: and, or, not
        if op == "and":
            return bool(cls._eval_node(node.left, item)) and bool(cls._eval_node(node.right, item))
        if op == "or":
            return bool(cls._eval_node(node.left, item)) or bool(cls._eval_node(node.right, item))
        if op == "not":
            return not bool(cls._eval_node(node.right, item))

        # Comparison: eq, ne, gt, ge, lt, le
        left = cls._eval_node(node.left, item)
        right = cls._eval_node(node.right, item)

        # Case-insensitive string comparison for LLM ergonomics.
        # Without this, ``title eq 'hello'`` would not match ``title='Hello'``.
        if isinstance(left, str) and isinstance(right, str):
            left = left.lower()
            right = right.lower()

        if op == "eq":
            return left == right
        if op == "ne":
            return left != right
        if op in ("gt", "ge", "lt", "le"):
            if left is None or right is None:
                return False
            if op == "gt":
                return left > right
            if op == "ge":
                return left >= right
            if op == "lt":
                return left < right
            return left <= right

        raise ValueError(
            f"Unsupported filter operator: {op!r}. "
            "Supported: eq, ne, gt, ge, lt, le, and, or, not."
        )

    @classmethod
    def filter_items(
        cls,
        items: list,
        *,
        filter: str | None = None,
        search: str | None = None,
        search_fields: list[str] | None = None,
    ) -> list:
        """Apply OData-like $filter and $search expressions client-side.

        The Graph Planner API ignores $filter/$search on task endpoints, so
        this method applies them in Python after all items have been fetched.
        Parsing uses odata-v4-query's ODataFilterParser; evaluation walks the
        AST against item attributes using getattr().

        Args:
            items: List of Kiota model objects (e.g. PlannerTask).
            filter: OData $filter expression string, e.g. "title eq 'Hello'".
            search: Free-text search term. Matched as a case-insensitive
                substring against each field in *search_fields*.
            search_fields: Attribute names (camelCase) to search. Defaults
                to ["title"] when *search* is provided.

        Returns:
            Filtered list. May be empty.

        Raises:
            ValueError: If the filter expression uses unsupported syntax.
        """
        if not items:
            return items

        result = items

        if filter:
            ast = cls._filter_parser.parse(filter)
            result = [item for item in result if cls._eval_node(ast, item)]

        if search:
            # Strip surrounding quotes if present — LLM callers often pass
            # search terms quoted (e.g. ``"Project"``).
            term = search.strip().strip('"').strip("'").lower()
            # Treat an empty normalized term as "no search". Without this,
            # `'' in <field>` matches every item and turns quoted-empty input
            # into an accidental match-all filter.
            # Docs: https://docs.python.org/3/reference/expressions.html#membership-test-operations
            if not term:
                return result
            fields = search_fields or ["title"]
            result = [
                item for item in result
                if any(
                    term in str(cls._get_attr(item, f) or "").lower()
                    for f in fields
                )
            ]

        return result

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
                raise RuntimeError(self.clean_graph_error(exc)) from None
            try:
                return await operation(await refresh())
            except ODataError as retry_exc:
                raise RuntimeError(self.clean_graph_error(retry_exc)) from None

    @staticmethod
    def clean_graph_error(exc: ODataError) -> str:
        """Format a Graph API error as a user-safe string without internal IDs.

        ODataError.__str__() includes client_request_id, datetime, and InnerError
        objects that leak server internals. This helper extracts only the HTTP
        status and user-facing message so callers can re-raise a clean exception.
        Without this, mask_error_details=True on FastMCP still exposes the raw
        repr because it masks tracebacks, not exception messages.
        """
        status = exc.response_status_code or "unknown"
        msg = (exc.error.message if exc.error else None) or "Unknown error"
        return f"Graph API error ({status}): {msg}"
