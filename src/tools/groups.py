from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.users.item.transitive_member_of.graph_group.graph_group_request_builder import GraphGroupRequestBuilder

from ..deps import get_optional_context
from ..graph_client_manager import graph_client_manager
from ..services.group_service import GroupService

groups_router = FastMCP("groups")


@groups_router.tool(name="list_my_groups", annotations={"readOnlyHint": True})
async def list_my_groups(
    select: str | None = "id,displayName,mail",
    filter: str | None = None,
    expand: str | None = None,
) -> list[dict] | None:
    """List all Microsoft 365 groups the authenticated user is a member of.
    
    Args:
        select: Comma-separated list of Group fields to include. Default is "id,displayName,mail". Pass "*all" for all fields.
        filter: OData filter string to filter groups, e.g. "startsWith(displayName,'Project')". Use OData filter syntax.
        expand: Comma-separated list of related entities to expand. Use OData expand syntax, e.g. "members($select=id,displayName)".

    Returns:
        A list of Group objects, or None if the user belongs to no groups.
        Each group's id field can be used as the group_id for list_group_plans and create_plan.
    """
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    ctx = get_optional_context()

    if ctx is not None:
        await ctx.debug("Fetching Microsoft 365 group memberships for the authenticated user")

    # "*all" is a sentinel that bypasses $select entirely so Graph returns
    # every field. Passing select=None to the SDK achieves this — the SDK
    # omits the $select query parameter from the request URL.
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
    # $expand also requires a list[str]. Split the caller-supplied CSV the same
    # way as $select so callers don't need to know the SDK's internal shape.
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
        groups = await GroupService(graph_client, serialize=True).paginate(
            graph_client.me.transitive_member_of.graph_group,
            RequestConfiguration(
                query_parameters=GraphGroupRequestBuilder.GraphGroupRequestBuilderGetQueryParameters(
                    select=select_fields,
                    filter=filter,
                    expand=expand_fields,
                )
            ),
        )

    if ctx is not None:
        await ctx.debug(f"Found {len(groups)} group(s)")

    return groups or None
