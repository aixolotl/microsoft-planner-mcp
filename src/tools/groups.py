from __future__ import annotations

from pydantic import Field
from typing import Annotated

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.headers_collection import HeadersCollection
from msgraph.generated.models.group import Group
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.users.item.transitive_member_of.graph_group.graph_group_request_builder import GraphGroupRequestBuilder

from ..deps import get_optional_context
from ..graph_client_manager import graph_client_manager
from ..services.planner_service import PlannerService

groups_router = FastMCP("groups")


@groups_router.tool(
    name="list_my_groups",
    description="List all Microsoft 365 groups the authenticated user is a member of. Note: with default permissions, displayName and other properties are not returned, but they can be used in filter and search.",
    tags={"groups", "read"},
    annotations={"readOnlyHint": True},
)
async def list_my_groups(
    select: Annotated[
        str | None, 
        Field(description="Optional comma-separated list of Group fields to include. Pass '*all' for all fields.", 
              default="id,displayName,mail")] = "id,displayName,mail",
    filter: Annotated[
        str | None, 
        Field(description="OData filter string, e.g. \"startsWith(displayName,'Project')\".",
              examples=["startsWith(displayName,'Project')", "displayName eq 'Project X'"])] = None,
    search: Annotated[
        str | None,
        Field(description="OData search string to perform free-text search across multiple fields. e.g. \"displayName:Project\" for searching group name containing 'Project'.",
              examples=["\"displayName:Project\"", "\"description:Something else\""])] = None,
) -> list[dict] | None:
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
    
    async with graph_client_manager.for_user(token.token) as graph_client:
        svc = PlannerService(graph_client)
        # /me/memberOf returns all directory objects the user belongs to, not
        # just groups. We use /me/transitiveMemberOf/microsoft.graph.group to
        # filter to M365 groups only via OData cast. Without the cast, the
        # response includes roles, admin units, and other non-group objects that
        # cannot be passed to list_group_plans or create_plan.
        # Docs: https://learn.microsoft.com/en-us/graph/api/user-list-transitivememberof
        config = RequestConfiguration(
            query_parameters=GraphGroupRequestBuilder.GraphGroupRequestBuilderGetQueryParameters(
                select=select_fields,
                filter=filter,
                search=search, 
                count=True if filter or search else None,
            ),
        )
        # $filter on /me/transitiveMemberOf/microsoft.graph.group is an
        # advanced query — Graph requires ConsistencyLevel: eventual and
        # $count=true or it returns 400 Request_UnsupportedQuery. Without
        # these headers, every filter value is rejected.
        # Docs: https://learn.microsoft.com/en-us/graph/aad-advanced-queries
        if filter or search:
            config.headers = HeadersCollection()
            config.headers.try_add("ConsistencyLevel", "eventual")
        try:
            groups = await PlannerService.paginate(
                graph_client.me.transitive_member_of.graph_group,
                config,
            )
        except ODataError as exc:
            raise RuntimeError(PlannerService.clean_graph_error(exc)) from None

    if ctx is not None:
        await ctx.debug(f"Found {len(groups)} group(s)")

    return svc.serialize_graph_list(groups) if groups else None
