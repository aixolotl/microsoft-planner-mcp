from __future__ import annotations

from typing import Annotated

from pydantic import Field

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.headers_collection import HeadersCollection
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.users.item.transitive_member_of.graph_group.graph_group_request_builder import GraphGroupRequestBuilder

from ..deps import get_optional_context
from ..graph_client_manager import graph_client_manager
from ..services.group_service import GroupService

groups_router = FastMCP("groups")


@groups_router.tool(name="list_my_groups", annotations={"readOnlyHint": True})
async def list_my_groups(
    select: Annotated[
        str | None,
        # FastMCP/Pydantic enforces min_length=1 at the MCP protocol level,
        # rejecting "" before the function body runs.
        # Docs: https://gofastmcp.com/servers/tools#advanced-metadata-with-field
        Field(
            description=(
                "Comma-separated Group fields to return. "
                "Default: 'id,displayName,mail'. Pass '*all' for all fields."
            ),
            min_length=1,
        ),
    ] = "id,displayName,mail",
    expand: Annotated[
        str | None,
        Field(
            description=(
                "Navigation properties to expand, e.g. \"members($select=id,displayName)\". "
                "Expanding members or owners requires GroupMember.Read.All."
            ),
        ),
    ] = None,
) -> list[dict] | None:
    """List all Microsoft 365 groups the authenticated user is a member of.

    Args:
        select: Comma-separated list of Group fields to include. Default is "id,displayName,mail". Pass "*all" for all fields.
        expand: Comma-separated list of related entities to expand. Use OData expand syntax, e.g. "members($select=id,displayName)".

    Returns:
        A list of Group objects, or None if the user belongs to no groups.
        Each group's id field can be used as the group_id for list_group_plans
        and create_plan.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    ctx = get_optional_context()

    if ctx is not None:
        await ctx.debug("Fetching Microsoft 365 group memberships for the authenticated user")

    # "" is rejected at the MCP protocol level by Field(min_length=1) above.
    # "*all" sentinel bypasses $select so Graph returns every field.
    # Docs: https://learn.microsoft.com/en-us/graph/query-parameters#select-parameter
    if select == "*all":
        select = None

    # Kiota QueryParameters dataclasses require list[str] for $select, not a
    # comma-separated string. Without the split, "id,displayName" would be sent
    # as a single field name rather than two separate ones.
    # Docs: https://github.com/microsoft/kiota-abstractions-python
    select_fields = (
        [field.strip() for field in select.split(",") if field.strip()]
        if select is not None
        else None
    )    # Guard: whitespace-only ("   ") and comma-only (", ,") strings survive
    # min_length=1 but resolve to zero tokens after split — they would silently
    # omit $select and return ALL fields. Reject them explicitly.
    # Docs: https://gofastmcp.com/servers/tools#advanced-metadata-with-field
    if select_fields is not None and not select_fields:
        raise ValueError(
            f"select resolved to no fields after parsing (input: {select!r}). "
            "Pass None or '*all' to return all fields."
        )    # $expand also requires a list[str]. Split the caller-supplied CSV the same
    # way as $select so callers don’t need to know the SDK’s internal shape.
    expand_fields = (
        [field.strip() for field in expand.split(",") if field.strip()]
        if expand is not None
        else None
    )
    
    async with graph_client_manager.for_user(token.token) as graph_client:
        # /me/memberOf returns all directory objects the user belongs to, not
        # just groups. We use /me/transitiveMemberOf/microsoft.graph.group to
        # filter to M365 groups only via OData cast. Without the cast, the
        # response includes roles, admin units, and other non-group objects that
        # cannot be passed to list_group_plans or create_plan.
        # Docs: https://learn.microsoft.com/en-us/graph/api/user-list-transitivememberof
        #
        # ConsistencyLevel: eventual is REQUIRED for transitiveMemberOf when
        # using any query parameters ($select, $filter, $orderby) or OData cast.
        # Without it, Graph silently returns only id + @odata.type for each
        # group, discarding all other fields (displayName, mail, etc.) because
        # the eventual-consistency index is not consulted.
        # Docs: https://learn.microsoft.com/en-us/graph/api/user-list-transitivememberof#optional-query-parameters
        # Docs: https://learn.microsoft.com/en-us/graph/aad-advanced-queries
        headers = HeadersCollection()
        headers.add("ConsistencyLevel", "eventual")
        try:
            groups = await GroupService(graph_client, serialize=True).paginate(
                graph_client.me.transitive_member_of.graph_group,
                RequestConfiguration(
                    headers=headers,
                    query_parameters=GraphGroupRequestBuilder.GraphGroupRequestBuilderGetQueryParameters(
                        select=select_fields,
                        expand=expand_fields,
                    )
                ),
            )
        except ODataError as exc:
            # Convert all ODataErrors to ValueError so the LLM receives readable
            # text rather than a raw APIError stack trace.
            # Docs: https://learn.microsoft.com/en-us/graph/api/user-list-transitivememberof#optional-query-parameters
            # Docs: https://learn.microsoft.com/en-us/graph/errors#http-status-codes
            code = exc.response_status_code
            msg = exc.error.message if exc.error else exc.primary_message
            if code == 400:
                # $select or $expand referenced an unknown field or navigation
                # property — Graph returns 400 with a descriptive error message.
                raise ValueError(f"Invalid query parameter: {msg}") from exc
            if code == 403:
                # User lacks permission to expand (e.g. GroupMember.Read.All
                # required for $expand=members or $expand=owners).
                raise ValueError(
                    f"Insufficient privileges: {msg}. "
                    "Expanding member or owner navigation properties requires "
                    "GroupMember.Read.All or Member.Read.Hidden.All."
                ) from exc
            if code == 404:
                # The resource was not found — the endpoint may not be available
                # for this user, or a navigation property in $expand does not
                # exist on the Group at the transitiveMemberOf endpoint.
                raise ValueError(
                    f"Resource not found: {msg}."
                ) from exc
            raise

    if ctx is not None:
        await ctx.debug(f"Found {len(groups)} group(s)")

    return groups or None
