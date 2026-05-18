from __future__ import annotations

import re
from typing import Annotated

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.headers_collection import HeadersCollection
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.users.users_request_builder import UsersRequestBuilder
from pydantic import Field

from ..deps import get_optional_context
from ..graph_client_manager import graph_client_manager
from ..services.planner_service import PlannerService

users_router = FastMCP("users")

# Graph enforces a 2 048-character limit on the query string portion of a
# request URL. A $filter expression built from many GUIDs or e-mail addresses
# can exceed this limit, causing a 414 URI Too Long response. We cap filter
# construction at this threshold and silently drop IDs that would push it over.
# Docs: https://learn.microsoft.com/en-us/graph/api/user-list
_MAX_FILTER_LEN = 2048

# Standard UUID format: 8-4-4-4-12 hex digits. Without this, a value
# containing OData control characters (e.g. single quotes) could break or
# alter the $filter expression.
# Docs: https://datatracker.ietf.org/doc/html/rfc4122
_GUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

# UPN / email: local part @ domain. Rejects OData control characters (single
# quotes, whitespace, parentheses) that could break or alter the $filter
# expression. Intentionally permissive on valid chars — the server will reject
# truly invalid addresses anyway.
# Docs: https://learn.microsoft.com/en-us/entra/identity/users/groups-dynamic-membership#rules-for-devices
_EMAIL_RE = re.compile(r"^[^'\s()@]+@[^'\s()@]+$")


def _build_id_filter(guids: list[str] | None, emails: list[str] | None) -> str:
    """Build an OData $filter expression for id and userPrincipalName lookups.

    Accumulates ``id eq 'guid'`` and ``userPrincipalName eq 'email'`` clauses
    joined by `` or `` until the expression would exceed _MAX_FILTER_LEN. IDs
    that would push the string over the limit are silently dropped — callers
    should batch very large lists themselves.
    """
    clauses: list[str] = []
    sep = " or "

    for guid in (guids or []):
        if not _GUID_RE.match(guid):
            raise ValueError(f"Invalid GUID format: {guid!r}")
        clause = f"id eq '{guid}'"
        needed = len(clause) + (len(sep) if clauses else 0)
        if sum(len(c) for c in clauses) + len(sep) * max(len(clauses) - 1, 0) + needed > _MAX_FILTER_LEN:
            break
        clauses.append(clause)

    for email in (emails or []):
        if not _EMAIL_RE.match(email):
            raise ValueError(f"Invalid email format: {email!r}")
        clause = f"userPrincipalName eq '{email}'"
        needed = len(clause) + (len(sep) if clauses else 0)
        if sum(len(c) for c in clauses) + len(sep) * max(len(clauses) - 1, 0) + needed > _MAX_FILTER_LEN:
            break
        clauses.append(clause)

    return sep.join(clauses)


def _normalize_search(search: str) -> str:
    """Normalise a search string for the Graph users $search parameter.

    Graph requires the $search value to be wrapped in double quotes with a
    'field:value' format, e.g. ``"displayName:Alice"``. This helper accepts
    plain strings and makes callers' lives easier in two ways:

    - Strips surrounding quotes the caller may have already added.
    - Defaults to ``displayName`` when no field prefix is present, so passing
      ``"alice"`` is equivalent to passing ``"displayName:alice"``.
    - Re-wraps the result in double quotes as Graph requires.

    Examples::

        _normalize_search("alice")              # → '"displayName:alice"'
        _normalize_search('"alice"')            # → '"displayName:alice"'
        _normalize_search("surname:Smith")      # → '"surname:Smith"'
        _normalize_search('"displayName:Bob"')  # → '"displayName:Bob"'
    """
    # Strip outer whitespace then surrounding double/single quotes so we
    # always work with the bare value. Without this, a caller who already
    # wraps the term in quotes (as the old API required) would produce
    # '"displayName:"displayName:Alice""' after re-wrapping.
    value = search.strip().strip('"').strip("'")
    if ":" not in value:
        value = f"displayName:{value}"
    return f'"{value}"'


@users_router.tool(
    name="list_users",
    description=(
        "Retrieve Microsoft 365 users by GUID, e-mail, or free-text search. "
        "Useful for resolving the user GUIDs that appear in task assignment objects to display names. "
        "Requires the User.ReadBasic.All delegated permission. "
        "Supply 'guids' and/or 'emails' to look up specific users, or 'search' to find users by name "
        "(e.g. \"Alice\" or \"surname:Smith\"). A plain string without a field prefix defaults to displayName. "
        "When none of those are provided the tool returns the first 'top' users in the directory."
    ),
    tags={"users", "read"},
    annotations={"readOnlyHint": True},
)
async def list_users(
    select: Annotated[
        str | None,
        Field(
            description=(
                "Comma-separated list of User fields to include. "
                "Pass '*all' for all fields. "
                "Default: id,displayName,mail,userPrincipalName."
            ),
            default="id,displayName,mail,userPrincipalName",
        ),
    ] = "id,displayName,mail,userPrincipalName",
    search: Annotated[
        str | None,
        Field(
            description=(
                "Free-text search for users. A plain string (e.g. \"Alice\") searches by displayName. "
                "Use 'field:value' syntax to target a specific field (e.g. \"surname:Smith\"). "
                "Surrounding quotes are stripped automatically."
            ),
            examples=['"Alice"', "surname:Smith", '"displayName:Bob"'],
        ),
    ] = None,
    guids: Annotated[
        list[str] | None,
        Field(
            description=(
                "List of user object GUIDs to look up. "
                "Translated to an OData $filter expression. "
                "Large lists are silently truncated to stay within the 2 048-character URL limit."
            ),
        ),
    ] = None,
    emails: Annotated[
        list[str] | None,
        Field(
            description=(
                "List of user principal names (UPNs / e-mail addresses) to look up. "
                "Translated to an OData $filter expression. "
                "Large lists are silently truncated to stay within the 2 048-character URL limit."
            ),
        ),
    ] = None,
    top: Annotated[
        int,
        Field(
            description="Maximum number of users to return. Ignored when 'guids' or 'emails' are provided. Default: 10.",
            ge=1,
            le=999,
            default=10,
        ),
    ] = 10,
) -> list[dict] | None:
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    ctx = get_optional_context()

    if ctx is not None:
        await ctx.debug("Fetching Microsoft 365 users")

    # "*all" is a sentinel that bypasses $select so Graph returns every field.
    # Passing select=None to the SDK achieves this — the SDK omits the $select
    # query parameter from the request URL. Without this, callers would have to
    # pass a comprehensive field list to get the full user object.
    if select == "*all":
        select = None

    # The Graph SDK's $select parameter requires a list of field names, not a
    # comma-separated string. We split here so callers can use the natural
    # "id,displayName,mail" syntax without needing to know the SDK's internal shape.
    select_fields = (
        [field.strip() for field in select.split(",") if field.strip()]
        if select is not None
        else None
    )

    use_filter = bool(guids or emails)
    use_search = bool(search) and not use_filter

    # Normalise the search string to Graph's required "field:value" format
    # before it is forwarded to the SDK. Without this, plain terms like "Alice"
    # would be sent as-is and Graph would return 400 Bad Request.
    if use_search:
        search = _normalize_search(search)  # type: ignore[arg-type]

    async with graph_client_manager.for_user(token.token) as graph_client:
        svc = PlannerService(graph_client)

        if use_filter:
            filter_expr = _build_id_filter(guids, emails)
            config = RequestConfiguration(
                query_parameters=UsersRequestBuilder.UsersRequestBuilderGetQueryParameters(
                    select=select_fields,
                    filter=filter_expr,
                    # $filter on the users endpoint by id is an advanced query and
                    # requires ConsistencyLevel: eventual + $count=true. Without
                    # these, Graph returns 400 Request_UnsupportedQuery.
                    # Docs: https://learn.microsoft.com/en-us/graph/aad-advanced-queries
                    count=True,
                ),
            )
            config.headers = HeadersCollection()
            config.headers.try_add("ConsistencyLevel", "eventual")

        elif use_search:
            config = RequestConfiguration(
                query_parameters=UsersRequestBuilder.UsersRequestBuilderGetQueryParameters(
                    select=select_fields,
                    search=search,
                    # $search on the users endpoint is an advanced query and
                    # requires ConsistencyLevel: eventual + $count=true. Without
                    # these, Graph returns 400 Request_UnsupportedQuery.
                    # Docs: https://learn.microsoft.com/en-us/graph/aad-advanced-queries
                    count=True,
                    top=top,
                ),
            )
            config.headers = HeadersCollection()
            config.headers.try_add("ConsistencyLevel", "eventual")

        else:
            config = RequestConfiguration(
                query_parameters=UsersRequestBuilder.UsersRequestBuilderGetQueryParameters(
                    select=select_fields,
                    top=top,
                ),
            )

        try:
            users = await PlannerService.paginate(
                graph_client.users,
                config,
                # Enforce the top ceiling client-side across all pages so that
                # paginate() stops following @odata.nextLink once enough items
                # have been collected. In filter mode we want every matching
                # user regardless of count, so top is only applied for the
                # search and default (browse) modes.
                top=None if use_filter else top,
            )
        except ODataError as exc:
            raise RuntimeError(PlannerService.clean_graph_error(exc)) from None

    if ctx is not None:
        await ctx.debug(f"Found {len(users)} user(s)")

    return svc.serialize_graph_list(users) if users else None
