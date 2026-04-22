from __future__ import annotations

from typing import Annotated
from urllib.parse import unquote

from pydantic import Field

from fastmcp import FastMCP
from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.planner_assignments import PlannerAssignments
from msgraph.generated.models.planner_checklist_items import PlannerChecklistItems
from msgraph.generated.models.planner_external_references import PlannerExternalReferences
from msgraph.generated.models.planner_task import PlannerTask
from msgraph.generated.models.planner_task_details import PlannerTaskDetails
from msgraph.generated.models.planner_preview_type import PlannerPreviewType
# Two different SDK-generated request builders share the class name
# "TasksRequestBuilder" but live in different URL namespaces:
#   - users/.../planner/tasks  (used by list_my_tasks — tasks assigned to me)
#   - planner/plans/{id}/tasks (used by list_tasks — tasks within a plan)
# Both are imported so we can build the correct $select query parameters for
# each endpoint. The alias avoids a name collision at import time.
from msgraph.generated.users.item.planner.tasks.tasks_request_builder import TasksRequestBuilder
from msgraph.generated.planner.plans.item.tasks.tasks_request_builder import TasksRequestBuilder as PlanTasksRequestBuilder

from ..deps import get_optional_context
from ..graph_client_manager import graph_client_manager
from ..services.planner_service import PlannerService

tasks_router = FastMCP("tasks")

# Fields on PlannerTask that are OData internals or navigation relationships
# rather than selectable data properties. Excluded from list_task_fields so
# callers only see fields valid in $select or usable as task attributes.
# Docs: https://learn.microsoft.com/en-us/graph/api/resources/plannertask#relationships
_TASK_NON_DATA_FIELDS = frozenset({
    "@odata.type",
    "assignedToTaskBoardFormat",  # navigation: board view format
    "bucketTaskBoardFormat",       # navigation: board view format
    "progressTaskBoardFormat",     # navigation: board view format
    "details",                     # navigation: PlannerTaskDetails sub-resource
})

# Fields on PlannerTaskDetails that are OData internals, not data properties.
_DETAIL_NON_DATA_FIELDS = frozenset({"@odata.type", "id"})

# Annotations for each PlannerTask and PlannerTaskDetails field.
# Field names come from get_field_deserializers() (SDK-derived). This table
# only adds the metadata the SDK doesn't expose: type, description, writability.
# Fields absent from this table fall back to type="unknown", writable=False.
# Docs: https://learn.microsoft.com/en-us/graph/api/resources/plannertask
#       https://learn.microsoft.com/en-us/graph/api/resources/plannertaskdetails
_FIELD_ANNOTATIONS: dict[str, dict] = {
    # ----- PlannerTask fields -----
    "id":                       {"type": "string",         "writable": False, "description": "Unique identifier of the task (28 chars)."},
    "title":                    {"type": "string",         "writable": True,  "description": "Title of the task."},
    "planId":                   {"type": "string",         "writable": True,  "description": "ID of the plan this task belongs to."},
    "bucketId":                 {"type": "string",         "writable": True,  "description": "ID of the bucket this task is placed in."},
    "orderHint":                {"type": "string",         "writable": True,  "description": "Hint used to order tasks in list view."},
    "assigneePriority":         {"type": "string",         "writable": True,  "description": "Hint for ordering within an assignee's task list."},
    "percentComplete":          {"type": "integer",        "writable": True,  "description": "Completion percentage (0–100). Set to 100 to mark complete."},
    "priority":                 {"type": "integer",        "writable": True,  "description": "Priority 0 (urgent)–10 (low). Planner maps: 1=urgent, 3=important, 5=medium, 9=low."},
    "startDateTime":            {"type": "DateTimeOffset", "writable": True,  "description": "ISO 8601 UTC start date/time."},
    "dueDateTime":              {"type": "DateTimeOffset", "writable": True,  "description": "ISO 8601 UTC due date/time."},
    "completedDateTime":        {"type": "DateTimeOffset", "writable": False, "description": "Read-only. When percentComplete was set to 100."},
    "createdDateTime":          {"type": "DateTimeOffset", "writable": False, "description": "Read-only. When the task was created."},
    "hasDescription":           {"type": "boolean",        "writable": False, "description": "Read-only. True if the task details description is non-empty."},
    "previewType":              {"type": "string",         "writable": True,  "description": "Preview style: automatic | noPreview | checklist | description | reference."},
    "referenceCount":           {"type": "integer",        "writable": False, "description": "Read-only. Number of external references."},
    "checklistItemCount":       {"type": "integer",        "writable": False, "description": "Read-only. Total checklist items."},
    "activeChecklistItemCount": {"type": "integer",        "writable": False, "description": "Read-only. Incomplete checklist items."},
    "appliedCategories":        {"type": "object",         "writable": True,  "description": "Categories applied to the task, keyed by category slot (e.g. {\"category3\": true}). Use list_plan_categories for label names."},
    "assignments":              {"type": "object",         "writable": True,  "description": "Map of user IDs to plannerAssignment objects."},
    "createdBy":                {"type": "object",         "writable": False, "description": "Read-only. Identity of the user who created the task."},
    "completedBy":              {"type": "object",         "writable": False, "description": "Read-only. Identity of the user who completed the task."},
    "conversationThreadId":     {"type": "string",         "writable": False, "description": "Thread ID of the conversation on the task."},
    # ----- PlannerTaskDetails fields (require get_task_details) -----
    # previewType appears on both resources but means the same thing; the
    # details entry shadows the task-level one only when detailed=True.
    "description":              {"type": "string",         "writable": True,  "description": "Plain-text task description (up to 2000 chars)."},
    "checklist":                {"type": "object",         "writable": True,  "description": "Checklist items keyed by GUID. Each item has title, isChecked, and orderHint."},
    "references":               {"type": "object",         "writable": True,  "description": "External references keyed by encoded URL. Each has alias, type, and previewPriority."},
}


# readOnlyHint=True signals to MCP clients that this tool never mutates state,
# allowing them to skip user confirmation prompts for read operations.
# Docs: https://gofastmcp.com/servers/tools#using-annotation-hints
@tasks_router.tool(
    name="list_my_tasks",
    description="List all Planner tasks assigned to the authenticated user across all plans.",
    tags={"tasks", "read"},
    annotations={"readOnlyHint": True},
)
async def list_my_tasks(
    select: Annotated[str | None, "Comma-separated list of PlannerTask fields to include. Pass '*all' for all fields."] = "*all",
    filter: Annotated[
        str | None,
        Field(
            description=(
                "OData filter expression. "
                "Supported operators: eq, ne, gt, ge, lt, le, and, or, not. "
                "Supported functions: startswith, endswith, contains. "
                "Example to get all incomplete tasks: \"percentComplete ne 100\"."
            ),
            examples=["startswith(title, 'Project')", "title eq 'Project X'", "percentComplete ne 100", "startswith(title, 'Project') and percentComplete eq 0"],
        ),
    ] = None,
    search: Annotated[
        str | None,
        Field(
            description=(
                "Free-text search term matched as a case-insensitive substring "
                "against the task title. Quotes are stripped automatically."
            ),
            examples=["Project", "Bug fix"],
        ),
    ] = None,
) -> list[dict] | None:
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    ctx = get_optional_context()

    if ctx is not None:
        await ctx.info("Fetching tasks assigned to the authenticated user")

    # "*all" is a sentinel that tells the tool to omit $select entirely so
    # Graph returns every task field. Without the sentinel, callers would have
    # to set select=None explicitly, which is less ergonomic for LLM agents.
    if select == "*all":
        select = None

    # The Graph SDK's $select parameter is a list[str], not a comma-separated
    # string. We split here so callers can use the natural "id,title" syntax.
    select_fields = (
        [field.strip() for field in select.split(",") if field.strip()]
        if select is not None
        else None
    )

    async with graph_client_manager.for_user(token.token) as graph_client:
        svc = PlannerService(graph_client)
        try:
            # Graph Planner ignores $filter and $search on /me/planner/tasks,
            # so we fetch all items then filter client-side. Only $select is
            # forwarded because it controls which fields Graph returns.
            # Docs: https://github.com/aixolotl/microsoft-planner-mcp/issues/29
            tasks = await PlannerService.paginate(
                graph_client.me.planner.tasks,
                RequestConfiguration(
                    query_parameters=TasksRequestBuilder.TasksRequestBuilderGetQueryParameters(
                        select=select_fields,
                    )
                ),
            )
        except ODataError as exc:
            raise RuntimeError(PlannerService.clean_graph_error(exc)) from None

    # Client-side filtering — applied after pagination so every item has
    # been fetched. filter_items() parses the OData expression via
    # odata-v4-query and evaluates it against each task's attributes.
    if filter or search:
        tasks = PlannerService.filter_items(
            tasks, filter=filter, search=search, search_fields=["title"]
        )

    if ctx is not None:
        await ctx.info(f"Found {len(tasks)} task(s) assigned to you")

    return svc.serialize_graph_list(tasks) if tasks else None


# readOnlyHint=True: this tool only reads from Graph, never writes.
# Docs: https://gofastmcp.com/servers/tools#using-annotation-hints
@tasks_router.tool(
    name="list_tasks",
    description="List all tasks in a Planner plan.",
    tags={"tasks", "read"},
    annotations={"readOnlyHint": True},
)
async def list_tasks(
    plan_id: Annotated[str, "The ID of the plan to list tasks for (from list_my_plans or list_group_plans)."],
    select: Annotated[str | None, "Comma-separated list of PlannerTask fields to include. Pass '*all' for all fields."] = "*all",
    filter: Annotated[
        str | None,
        Field(
            description=(
                "OData filter expression. "
                "Supported operators: eq, ne, gt, ge, lt, le, and, or, not. "
                "Supported functions: startswith, endswith, contains. "
                "Example to get all incomplete tasks: \"percentComplete ne 100\"."
            ),
            examples=["startswith(title, 'Project')", "title eq 'Project Task'", "percentComplete eq 0"],
        ),
    ] = None,
    search: Annotated[
        str | None,
        Field(
            description=(
                "Free-text search term matched as a case-insensitive substring "
                "against the task title. Quotes are stripped automatically."
            ),
            examples=["Project", "Bug fix"],
        ),
    ] = None,
) -> list[dict] | None:
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    ctx = get_optional_context()

    if ctx is not None:
        await ctx.info(f"Fetching tasks for plan {plan_id}")

    if select == "*all":
        select = None

    select_fields = (
        [field.strip() for field in select.split(",") if field.strip()]
        if select is not None
        else None
    )

    async with graph_client_manager.for_user(token.token) as graph_client:
        svc = PlannerService(graph_client)
        try:
            # Graph Planner ignores $filter and $search on plan-scoped tasks,
            # so we fetch all items then filter client-side. Only $select is
            # forwarded because it controls which fields Graph returns.
            # Docs: https://github.com/aixolotl/microsoft-planner-mcp/issues/29
            tasks = await PlannerService.paginate(
                graph_client.planner.plans.by_planner_plan_id(plan_id).tasks,
                RequestConfiguration(
                    query_parameters=PlanTasksRequestBuilder.TasksRequestBuilderGetQueryParameters(
                        select=select_fields,
                    )
                ),
            )
        except ODataError as exc:
            raise RuntimeError(PlannerService.clean_graph_error(exc)) from None

    # Client-side filtering — applied after pagination so every item has
    # been fetched. filter_items() parses the OData expression via
    # odata-v4-query and evaluates it against each task's attributes.
    if filter or search:
        tasks = PlannerService.filter_items(
            tasks, filter=filter, search=search, search_fields=["title"]
        )

    if ctx is not None:
        await ctx.info(f"Found {len(tasks)} task(s) in plan {plan_id}")

    return svc.serialize_graph_list(tasks) if tasks else None


@tasks_router.tool(
    name="create_task",
    description="Create a new task in a Planner plan.",
    tags={"tasks", "write"},
)
async def create_task(
    plan_id: Annotated[str, "The ID of the plan to create the task in."],
    bucket_id: Annotated[str, "The ID of the bucket to place the task in (from list_buckets)."],
    title: Annotated[str, "The title of the task."],
    start_date_time: Annotated[str | None, "ISO 8601 start date (e.g. '2026-05-01T00:00:00'). Must not be after due_date_time."] = None,
    due_date_time: Annotated[str | None, "ISO 8601 due date (e.g. '2026-05-31T00:00:00')."] = None,
    percent_complete: Annotated[int, Field(description="Completion percentage (0–100).", ge=0, le=100)] | None = None,
    assign_user_ids: Annotated[list[str] | None, "List of user object IDs to assign to the task."] = None,
) -> dict | None:
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    start_dt = PlannerService.to_utc(start_date_time) if start_date_time else None
    due_dt = PlannerService.to_utc(due_date_time) if due_date_time else None
    if start_dt and due_dt and start_dt > due_dt:
        raise ValueError(f"start_date_time ({start_date_time}) must not be after due_date_time ({due_date_time})")

    body = PlannerTask()
    body.plan_id = plan_id
    body.bucket_id = bucket_id
    body.title = title
    if start_dt is not None:
        body.start_date_time = start_dt
    if due_dt is not None:
        body.due_date_time = due_dt
    if percent_complete is not None:
        body.percent_complete = percent_complete
    if assign_user_ids:
        body.assignments = PlannerAssignments(
            additional_data={
                user_id: {
                    "@odata.type": "#microsoft.graph.plannerAssignment",
                    # orderHint " !" is the Graph API sentinel meaning
                    # "place at the top of the order". It is a magic string
                    # defined by the Planner ordering algorithm; without it
                    # the API rejects the assignment with a 400 BadRequest.
                    # Docs: https://learn.microsoft.com/en-us/graph/api/resources/planner-order-hint-format
                    "orderHint": " !",
                }
                for user_id in assign_user_ids
            }
        )

    async with graph_client_manager.for_user(token.token) as graph_client:
        svc = PlannerService(graph_client)
        try:
            result = await graph_client.planner.tasks.post(body)
            return svc.serialize_graph_object(result) if result else None
        except ODataError as exc:
            # 403 from task creation usually means the user has hit the plan
            # task limit or lacks write permission. Convert to ValueError with a
            # readable message so the LLM gets a clear explanation. All other
            # status codes are re-raised so genuine failures surface normally.
            if exc.response_status_code == 403:
                code = exc.error.code if exc.error else None
                msg = exc.error.message if exc.error else exc.primary_message
                raise ValueError(f"Cannot create task ({code}): {msg}") from exc
            raise RuntimeError(PlannerService.clean_graph_error(exc)) from None


# readOnlyHint=True: this tool reads task details without any side effects.
# Docs: https://gofastmcp.com/servers/tools#using-annotation-hints
@tasks_router.tool(
    name="get_task_details",
    description="Get the full details for a Planner task: description, checklist items, and external references.",
    tags={"tasks", "read"},
    annotations={"readOnlyHint": True},
)
async def get_task_details(
    task_id: Annotated[str, "The ID of the task to retrieve details for (from list_my_tasks or list_tasks)."],
) -> dict | None:
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    ctx = get_optional_context()

    if ctx is not None:
        await ctx.debug(f"Fetching details for task {task_id}")

    async with graph_client_manager.for_user(token.token) as graph_client:
        svc = PlannerService(graph_client)
        try:
            result = await graph_client.planner.tasks.by_planner_task_id(task_id).details.get()
        except ODataError as exc:
            raise RuntimeError(PlannerService.clean_graph_error(exc)) from None

    return svc.serialize_graph_object(result) if result else None


@tasks_router.tool(
    name="update_task",
    description="Update a Planner task's basic fields. All fields are optional — only provided fields are changed.",
    tags={"tasks", "write"},
    annotations={"idempotentHint": True},
)
async def update_task(
    task_id: Annotated[str, "The ID of the task to update (from list_my_tasks or list_tasks)."],
    etag: Annotated[str, "The current @odata.etag of the task. Retries once automatically if stale (412/409)."],
    title: Annotated[str | None, "New title for the task."] = None,
    percent_complete: Annotated[int, Field(description="Completion percentage (0–100).", ge=0, le=100)] | None = None,
    due_date_time: Annotated[str | None, "ISO 8601 due date string (e.g. '2026-05-31T00:00:00')."] = None,
    bucket_id: Annotated[str | None, "ID of the bucket to move the task to."] = None,
    assignee_priority: Annotated[str | None, "Order hint string for sorting within the assignee's task list."] = None,
    assign_user_ids: Annotated[list[str] | None, "List of user object IDs to assign to the task."] = None,
    unassign_user_ids: Annotated[list[str] | None, "List of user object IDs to remove from the task."] = None,
) -> dict | None:
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    body = PlannerTask()
    for attr, value in [
        ("title", title),
        ("percent_complete", percent_complete),
        ("bucket_id", bucket_id),
        ("assignee_priority", assignee_priority),
        ("due_date_time", PlannerService.to_utc(due_date_time) if due_date_time is not None else None),
    ]:
        if value is not None:
            setattr(body, attr, value)
    if assign_user_ids or unassign_user_ids:
        assignment_data: dict = {
            user_id: {"@odata.type": "#microsoft.graph.plannerAssignment", "orderHint": " !"}
            for user_id in (assign_user_ids or [])
        } | {user_id: None for user_id in (unassign_user_ids or [])}
        body.assignments = PlannerAssignments(additional_data=assignment_data)

    async with graph_client_manager.for_user(token.token) as graph_client:
        svc = PlannerService(graph_client)
        item = graph_client.planner.tasks.by_planner_task_id(task_id)
        result = await svc.with_retry(
            etag,
            lambda e: item.patch(body, request_configuration=svc.make_config(e, prefer_representation=True)),
            lambda: svc.refresh_etag(item.get, f"task {task_id!r}"),
        )
        return svc.serialize_graph_object(result) if result else None


@tasks_router.tool(
    name="update_task_details",
    description="Update the details of a Planner task: description, checklist items, and external references.",
    tags={"tasks", "write"},
    annotations={"idempotentHint": True},
)
async def update_task_details(
    task_id: Annotated[str, "The ID of the task to update."],
    etag: Annotated[str, "The current @odata.etag of the task details resource (from get_task_details). Retries once automatically if stale (412/409)."],
    description: Annotated[str | None, "New plain-text description for the task."] = None,
    preview_type: Annotated[str | None, "Preview style shown in Planner UI. One of: automatic, noPreview, checklist, description, reference."] = None,
    checklist_items: Annotated[dict | None, "Dict keyed by checklist item GUID. Pass null for a key to delete that item."] = None,
    references: Annotated[dict | None, "Dict keyed by URL-encoded reference URL (periods to %2E, colons to %3A). Pass null for a key to delete that reference."] = None,
) -> dict | None:
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    ctx = get_optional_context()

    if ctx is not None:
        await ctx.info(f"Updating details for task {task_id}")

    body = PlannerTaskDetails()
    if description is not None:
        body.description = description
    if preview_type is not None:
        # PlannerPreviewType is a str enum — look up by value (case-sensitive).
        # Without this, callers would need to know the Python enum member name.
        # Docs: https://learn.microsoft.com/en-us/graph/api/plannertaskdetails-update
        body.preview_type = PlannerPreviewType(preview_type)
    if checklist_items is not None:
        # Copy to avoid mutating the caller's dict when injecting @odata.type.
        # Without the copy, callers that reuse the dict would see unexpected
        # side effects (extra keys added silently).
        checklist_items = {k: ({**v} if isinstance(v, dict) else v) for k, v in checklist_items.items()}
        # The Graph Planner API requires @odata.type on every checklist item
        # in the PATCH body. Without it, Graph returns 400: "The given untyped
        # value … is invalid. Consider using a OData type annotation explicitly."
        # Docs: https://learn.microsoft.com/en-us/graph/api/plannertaskdetails-update
        for value in checklist_items.values():
            if isinstance(value, dict) and "@odata.type" not in value:
                value["@odata.type"] = "microsoft.graph.plannerChecklistItem"
        body.checklist = PlannerChecklistItems(additional_data=checklist_items)
    if references is not None:
        # Graph Planner requires reference keys to encode only ":" → %3A and
        # "." → %2E. Slashes and other URL characters stay literal — the docs
        # example shows "http%3A//developer%2Emicrosoft%2Ecom", NOT full
        # percent-encoding.
        # unquote() first normalises pre-encoded keys (e.g. "https%3A//…")
        # back to raw form so the subsequent replace is idempotent — callers
        # can send raw or pre-encoded URLs and get the same result.
        # Docs: https://learn.microsoft.com/en-us/graph/api/plannertaskdetails-update
        references = {
            unquote(k).replace(":", "%3A").replace(".", "%2E"): ({**v} if isinstance(v, dict) else v)
            for k, v in references.items()
        }
        # Same requirement for external references — each needs @odata.type.
        # Without it, Graph returns 400. Null values (used to delete a ref)
        # are skipped since they carry no properties.
        # Docs: https://learn.microsoft.com/en-us/graph/api/plannertaskdetails-update
        for value in references.values():
            if isinstance(value, dict) and "@odata.type" not in value:
                value["@odata.type"] = "microsoft.graph.plannerExternalReference"
        body.references = PlannerExternalReferences(additional_data=references)

    async with graph_client_manager.for_user(token.token) as graph_client:
        svc = PlannerService(graph_client)
        item = graph_client.planner.tasks.by_planner_task_id(task_id).details
        result = await svc.with_retry(
            etag,
            lambda e: item.patch(body, request_configuration=svc.make_config(e, prefer_representation=True)),
            lambda: svc.refresh_etag(item.get, f"task details {task_id!r}"),
        )
        return svc.serialize_graph_object(result) if result else None


@tasks_router.tool(
    name="list_task_fields",
    description=(
        "Return metadata for every field on a Planner task. Each entry includes the field "
        "name (camelCase, usable in $select), its data type, a description, whether it is "
        "writable, and whether it lives on the task resource directly or on the separate "
        "PlannerTaskDetails sub-resource (requiring a get_task_details call)."
    ),
    tags={"tasks", "read"},
    annotations={"readOnlyHint": True},
)
async def list_task_fields() -> list[dict]:
    # Field names come from the SDK deserializer registry so they stay in sync
    # with the installed msgraph-sdk version automatically. _FIELD_ANNOTATIONS
    # adds type/description/writability — metadata the SDK doesn't expose.
    # Fields missing from the annotation table get sensible defaults so we
    # never silently drop a field the SDK adds in a future version.
    # Docs: https://learn.microsoft.com/en-us/graph/api/resources/plannertask
    #       https://learn.microsoft.com/en-us/graph/api/resources/plannertaskdetails
    task_names = sorted(
        k for k in PlannerTask().get_field_deserializers()
        if k not in _TASK_NON_DATA_FIELDS
    )
    detail_names = sorted(
        k for k in PlannerTaskDetails().get_field_deserializers()
        if k not in _DETAIL_NON_DATA_FIELDS
    )

    def _entry(name: str, *, detailed: bool) -> dict:
        ann = _FIELD_ANNOTATIONS.get(name, {})
        # Use None when writability is not annotated so callers can distinguish
        # "unknown" from genuinely read-only. Without this, newly-added SDK
        # fields are mislabeled as non-writable.
        # Docs: https://learn.microsoft.com/en-us/graph/api/resources/plannertask
        writable = ann["writable"] if "writable" in ann else None
        return {
            "name":        name,
            "type":        ann.get("type", "unknown"),
            "description": ann.get("description", ""),
            "writable":    writable,
            "detailed":    detailed,
        }

    return (
        [_entry(n, detailed=False) for n in task_names]
        + [_entry(n, detailed=True) for n in detail_names]
    )


@tasks_router.tool(
    name="delete_task",
    description="Delete a Planner task.",
    tags={"tasks", "write"},
    annotations={"destructiveHint": True},
)
async def delete_task(
    task_id: Annotated[str, "The ID of the task to delete."],
    etag: Annotated[str, "The current @odata.etag of the task. Retries once automatically if stale (412/409)."],
) -> str:
    token = get_access_token()
    if token is None:
        raise AuthorizationError("No access token available")

    async with graph_client_manager.for_user(token.token) as graph_client:
        svc = PlannerService(graph_client)
        item = graph_client.planner.tasks.by_planner_task_id(task_id)
        await svc.with_retry(
            etag,
            lambda e: item.delete(request_configuration=svc.make_config(e)),
            lambda: svc.refresh_etag(item.get, f"task {task_id!r}"),
        )
    return f"Deleted task {task_id!r}."
