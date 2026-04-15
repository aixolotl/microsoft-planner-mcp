from __future__ import annotations

from typing import Any, Protocol

from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.request_adapter import RequestAdapter


class CollectionRequestBuilder(Protocol):
    """Structural type for Graph SDK request builders that return paged collections.

    WHY THIS EXISTS:
    kiota's `BaseRequestBuilder` only defines `__init__`, `path_parameters`,
    `url_template`, and `request_adapter`. It does NOT declare `.get()` — that
    method is generated per-resource on each concrete builder class (e.g.
    `TasksRequestBuilder`, `PlansRequestBuilder`) and is not part of the shared
    base. Typing `paginate(request_builder: BaseRequestBuilder, ...)` therefore
    produces an "unresolved attribute" error on `.get()`.

    Using `Any` would silence the error but abandons type safety entirely.

    This Protocol captures the exact structural surface that `paginate` needs:
    - `.get()` to fetch the first page, accepting an optional RequestConfiguration
    - `.request_adapter` so PageIterator (msgraph_core) can fetch subsequent pages

    All generated collection request builders satisfy this Protocol through
    structural subtyping (PEP 544) without needing to inherit from it.
    """

    request_adapter: RequestAdapter

    async def get(self, request_configuration: RequestConfiguration | None = None) -> Any: ...
