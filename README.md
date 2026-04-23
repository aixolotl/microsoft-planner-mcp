# Microsoft Planner MCP

An [MCP](https://modelcontextprotocol.io/) server that connects AI assistants to [Microsoft Planner](https://www.microsoft.com/en-us/microsoft-365/business/task-management-software). Ask your AI assistant to create tasks, organise plans, manage buckets, and more, all through natural language.

> **Note:** This MCP only supports Planner basic tasks and plans

Built with [FastMCP](https://gofastmcp.com) and authenticated via [Microsoft Entra ID](https://learn.microsoft.com/en-us/entra/identity-platform/) (Azure AD) using the On-Behalf-Of (OBO) flow to call [Microsoft Graph](https://learn.microsoft.com/en-us/graph/overview).

## Table of Contents

- [What Can It Do?](#what-can-it-do)
- [Prerequisites](#prerequisites)
- [Azure Entra ID Setup](#azure-entra-id-setup)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Server](#running-the-server)
- [Connecting an MCP Client](#connecting-an-mcp-client)
- [Available Tools](#available-tools)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)
- [Security](#security)

## What Can It Do?

Once connected, you can ask your AI assistant things like:

- *"Show me all my Planner tasks that are overdue"*
- *"Create a task called 'Prepare Q3 report' in the Marketing plan"*
- *"Move all incomplete tasks in the Sprint bucket to the Backlog bucket"*
- *"Mark the 'Update docs' task as complete"*

The AI assistant translates your request into the appropriate tool calls automatically.

## Prerequisites

- A **Microsoft 365** account with access to Microsoft Planner
- An **Azure Entra ID** (Azure AD) app registration (see [Azure Setup](#azure-entra-id-setup) below)
- An MCP-compatible client — for example:
  - [VS Code](https://code.visualstudio.com/)
  - [Claude Desktop](https://claude.ai/download)

To run the server locally you also need:

- [Python 3.12+](https://www.python.org/downloads/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (Python package manager)

Or, to run via Docker:

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/)

## Azure Entra ID Setup

An Azure app registration is required so the server can authenticate users and call Microsoft Graph on their behalf. You need admin access to an Azure Entra ID tenant (or ask your IT administrator).

### Step 1 — Register the Application

1. Go to the [Azure Portal](https://portal.azure.com/) → **Microsoft Entra ID** → **App registrations** → **New registration**
2. Enter a name (e.g. `Microsoft Planner MCP`)
3. Under **Supported account types**, choose the option appropriate for your organisation
4. Set the **Redirect URI** to **Web** → `http://localhost:8000/auth/callback`
5. Click **Register**

### Step 2 — Configure API Permissions

1. In your new app registration, go to **API permissions** → **Add a permission** → **Microsoft Graph** → **Delegated permissions**
2. Add these permissions:
   - `Tasks.ReadWrite` — read and write Planner tasks
   - `User.Read` — read the signed-in user's profile
3. Click **Grant admin consent** for your organisation

### Step 3 — Expose an API Scope

1. Go to **Expose an API**
2. Set the **Application ID URI** (accept the default `api://<client-id>` or customise it)
3. Click **Add a scope**:
   - Scope name: **`mcp-access`**
   - Who can consent: **Admins and users** (or **Admins only** if you prefer)
   - Fill in the display name and description
   - Set state to **Enabled**

### Step 4 — Set Token Version

1. Go to **Manifest** (or **Authentication** → **Advanced settings** in newer portal versions)
2. Set `"requestedAccessTokenVersion"` to **`2`**
3. Save

### Step 5 — Create a Client Secret

1. Go to **Certificates & secrets** → **New client secret**
2. Add a description and choose an expiry period
3. Copy the **Value** immediately (it is only shown once)

### Step 6 — Note Your IDs

You will need these three values for configuration:

| Value | Where to Find It |
|---|---|
| **Application (client) ID** | App registration → Overview |
| **Directory (tenant) ID** | App registration → Overview |
| **Client secret** | The value copied in Step 5 |

## Installation

### Option A — Local (Python + uv)

```bash
git clone https://github.com/aixolotl/microsoft-planner-mcp
cd microsoft-planner-mcp
uv sync
```

### Option B — Docker

No Python installation needed. See [Running with Docker](#running-with-docker) below.

## Configuration

Copy the example environment file and fill in your Azure credentials:

```bash
cp .env.example .env
```

Edit `.env`:

```ini
# Azure App Registration (required)
CLIENT_ID=your-app-client-id
CLIENT_SECRET=your-app-client-secret
TENANT_ID=your-azure-tenant-id

# Public URL of this server (used for OAuth redirect URI)
BASE_URL=http://localhost:8000

# JSON-encoded list of allowed CORS origins
# Include http://localhost:6274 if using MCP Inspector for testing
ALLOWED_ORIGINS=["http://localhost:8000","http://localhost:6274"]
```

## Running the Server

### Local

```bash
uv run uvicorn src.server:app --host 0.0.0.0 --port 8000
```

The MCP endpoint is available at `http://localhost:8000/mcp`. A health check endpoint is at `http://localhost:8000/health`.

### Running with Docker

```bash
docker compose up
```

This starts the MCP server on port 8000. The Docker Compose configuration also includes a [Jaeger](https://www.jaegertracing.io/) instance for trace visualisation (see [OpenTelemetry Tracing](#opentelemetry-tracing)).

## Connecting an MCP Client

Once the server is running, configure your MCP client to connect to it.

### VS Code (GitHub Copilot)

Add the following to your VS Code settings (`.vscode/settings.json` in your project, or your user settings):

```json
{
  "mcp": {
    "servers": {
      "planner": {
        "type": "http",
        "url": "http://localhost:8000/mcp"
      }
    }
  }
}
```

Then use Copilot Chat in **Agent mode** and ask it to interact with your Planner tasks. Copilot will discover the available tools automatically.

### Claude Desktop

Add the following to your Claude Desktop configuration file:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "planner": {
      "type": "streamable-http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

Restart Claude Desktop after saving. You will be prompted to authenticate with your Microsoft account on first use.

### Other MCP Clients

Any client that supports the **Streamable HTTP** transport can connect by pointing to `http://localhost:8000/mcp`. The server advertises OAuth metadata automatically — the client handles the authentication flow.

## Available Tools

All tools are available to your AI assistant automatically once connected. You don't need to call them directly — just describe what you want in natural language. The parameter details below are provided for reference and for client developers.

Read-only tools are annotated with `readOnlyHint: true` so clients can skip confirmation prompts. Destructive tools (deletes) are annotated with `destructiveHint: true`.

### User

#### `get_me`

Return the authenticated user's profile from Microsoft Graph.

- **Parameters:** None
- **Returns:** User profile object (id, displayName, mail, etc.) or `null`

### Groups

#### `list_my_groups`

List all Microsoft 365 groups the authenticated user is a member of.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `select` | string | No | Comma-separated fields to include (default: `id,displayName,mail`). Pass `*all` for all fields. |
| `filter` | string | No | OData filter expression, e.g. `startsWith(displayName,'Project')` |
| `search` | string | No | OData search string, e.g. `"displayName:Project"` |

- **Returns:** List of group objects or `null`

> **Note:** The Groups tool will not return details like name or mail of groups with the standard permissions `Tasks.ReadWrite` documented here, ***however*** searching and filtering still works, so you can find a group with a specific name using this tool.

### Plans

#### `list_my_plans`

List Planner plans shared with the authenticated user.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `select` | string | No | Comma-separated fields to include (default: `id,title,owner,createdBy,createdDateTime`). Pass `*all` for all fields. |

- **Returns:** List of plan objects or `null`

#### `list_group_plans`

List all Planner plans belonging to a Microsoft 365 group.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `group_id` | string | Yes | The object ID of the group (from `list_my_groups`) |
| `select` | string | No | Comma-separated fields to include (default: `id,title,owner,createdBy,createdDateTime`). Pass `*all` for all fields. |

- **Returns:** List of plan objects or `null`

#### `create_plan`

Create a new Planner plan for a Microsoft 365 group.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `group_id` | string | Yes | The object ID of the M365 group that will own the plan |
| `title` | string | Yes | Display title for the new plan |

- **Returns:** The created plan object or `null`

#### `delete_plan`

Delete a Planner plan.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `plan_id` | string | Yes | The ID of the plan to delete |
| `etag` | string | Yes | The current `@odata.etag` of the plan (retries once if stale) |

- **Returns:** Confirmation message

#### `list_plan_categories`

Get category label definitions for a Planner plan. Returns all 25 category slots with their key (e.g. `category1`) and display name.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `plan_id` | string | Yes | The ID of the plan |

- **Returns:** List of category objects (`key`, `display_name`) or `null`

> **Note:** The list of categories is static as the API does not return display names or colours. But this endpoint awaits Microsoft's future updates to the API to fully support categories.

### Buckets

#### `list_buckets`

List all buckets in a Planner plan.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `plan_id` | string | Yes | The ID of the plan |

- **Returns:** List of bucket objects or `null`

#### `create_bucket`

Create a new bucket in a Planner plan.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `plan_id` | string | Yes | The ID of the plan to create the bucket in |
| `name` | string | Yes | Display name for the new bucket |

- **Returns:** The created bucket object or `null`

#### `delete_bucket`

Delete a Planner bucket.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `bucket_id` | string | Yes | The ID of the bucket to delete |
| `etag` | string | Yes | The current `@odata.etag` of the bucket (retries once if stale) |

- **Returns:** Confirmation message

### Tasks

#### `list_my_tasks`

List all Planner tasks assigned to the authenticated user across all plans.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `select` | string | No | Comma-separated fields to include (default: `*all`). Pass `*all` for all fields. |
| `filter` | string | No | OData filter expression, e.g. `percentComplete ne 100` |
| `search` | string | No | Free-text search matched against the task title |

- **Returns:** List of task objects or `null`

#### `list_tasks`

List all tasks in a Planner plan.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `plan_id` | string | Yes | The ID of the plan |
| `select` | string | No | Comma-separated fields to include (default: `*all`). Pass `*all` for all fields. |
| `filter` | string | No | OData filter expression, e.g. `percentComplete eq 0` |
| `search` | string | No | Free-text search matched against the task title |

- **Returns:** List of task objects or `null`

#### `get_task_details`

Get the full details for a task: description, checklist items, and external references.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `task_id` | string | Yes | The ID of the task |

- **Returns:** Task details object (description, checklist, references) or `null`

#### `create_task`

Create a new task in a Planner plan.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `plan_id` | string | Yes | The ID of the plan |
| `bucket_id` | string | Yes | The ID of the bucket to place the task in |
| `title` | string | Yes | Title of the task |
| `start_date_time` | string | No | ISO 8601 start date (e.g. `2026-05-01T00:00:00`) |
| `due_date_time` | string | No | ISO 8601 due date (e.g. `2026-05-31T00:00:00`) |
| `percent_complete` | integer | No | Completion percentage, 0–100 |
| `assign_user_ids` | list[string] | No | User object IDs to assign to the task |

- **Returns:** The created task object or `null`

#### `update_task`

Update a task's basic fields. Only provided fields are changed.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `task_id` | string | Yes | The ID of the task |
| `etag` | string | Yes | The current `@odata.etag` of the task (retries once if stale) |
| `title` | string | No | New title |
| `percent_complete` | integer | No | Completion percentage, 0–100 |
| `due_date_time` | string | No | ISO 8601 due date |
| `bucket_id` | string | No | ID of the bucket to move the task to |
| `assignee_priority` | string | No | Order hint for sorting within the assignee's task list |
| `assign_user_ids` | list[string] | No | User IDs to assign |
| `unassign_user_ids` | list[string] | No | User IDs to remove |

- **Returns:** The updated task object or `null`

#### `update_task_details`

Update a task's description, checklist items, and external references.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `task_id` | string | Yes | The ID of the task |
| `etag` | string | Yes | The current `@odata.etag` of the task details resource (retries once if stale) |
| `description` | string | No | Plain-text description (up to 2000 characters) |
| `preview_type` | string | No | Preview style: `automatic`, `noPreview`, `checklist`, `description`, or `reference` |
| `checklist_items` | object | No | Dict keyed by checklist item GUID. Pass `null` for a key to delete that item. |
| `references` | object | No | Dict keyed by URL-encoded reference URL. Pass `null` for a key to delete that reference. |

- **Returns:** The updated task details object or `null`

#### `list_task_fields`

Return metadata for every field on a Planner task, including its data type, description, whether it is writable, and whether it requires a separate `get_task_details` call.

- **Parameters:** None
- **Returns:** List of field metadata objects (`name`, `type`, `description`, `writable`, `detailed`)

> **Note:** The list of fields is static as Planner Standard doesn't support custom fields. But this endpoint awaits Microsofts future updates to the API to fully support Planner Premium.

#### `delete_task`

Delete a Planner task.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `task_id` | string | Yes | The ID of the task |
| `etag` | string | Yes | The current `@odata.etag` of the task (retries once if stale) |

- **Returns:** Confirmation message

## Development

This section covers building, testing, and contributing to the project.

### Project Structure

```
src/
├── server.py                  # FastMCP app, middleware, route mounting
├── config.py                  # Settings via pydantic-settings
├── auth_provider.py           # Azure OAuth provider (OBO flow)
├── deps.py                    # Shared dependency helpers
├── graph_client_manager.py    # Singleton GraphClientManager with per-user OBO clients
├── telemetry.py               # OpenTelemetry setup
├── types.py                   # Shared structural types
├── services/
│   └── planner_service.py     # Business logic wrapping Graph SDK calls
└── tools/
    ├── me.py                  # get_me
    ├── groups.py              # list_my_groups
    ├── plans.py               # plan tools + list_plan_categories
    ├── tasks.py               # task tools + list_task_fields
    └── buckets.py             # bucket tools
tests/
├── conftest.py
├── test_buckets_tool.py
├── test_groups_tool.py
├── test_planner_service.py
├── test_plans_tool.py
└── test_tasks_tool.py
```

### Installing Dev Dependencies

```bash
uv sync --dev
```

### Running Tests

```bash
uv run pytest
```

With verbose output:

```bash
uv run pytest -v
```

### Architecture

The server is built with [FastMCP](https://gofastmcp.com) and uses these key patterns:

**Server composition** — Tools are split into five domain routers (`me`, `groups`, `plans`, `tasks`, `buckets`), each a standalone `FastMCP` instance [mounted](https://gofastmcp.com/servers/composition) on the main app. This keeps each domain's tools, imports, and tests isolated.

**Authentication** — The server uses FastMCP's [`OAuthProxy`](https://gofastmcp.com/servers/auth/oauth-proxy) pattern via a custom `AzureProvider`. Azure Entra ID does not support Dynamic Client Registration (DCR), so the provider acts as a DCR-compliant proxy facing MCP clients while using the pre-registered app credentials with Azure. When a tool call arrives, the server exchanges the MCP session token for a Microsoft Graph token via the [On-Behalf-Of flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow), scoped to `Tasks.ReadWrite` and `User.Read`.

**Middleware** — Five [built-in middleware](https://gofastmcp.com/servers/middleware#built-in-middleware) layers are stacked on the server (outermost first):

| Middleware | Purpose |
|---|---|
| `ErrorHandlingMiddleware` | Catches unhandled exceptions, logs full traces server-side, returns clean MCP errors |
| `SlidingWindowRateLimitingMiddleware` | 60 req/min per client to protect the Microsoft Graph quota |
| `TimingMiddleware` | Records wall-clock duration for every MCP operation |
| `StructuredLoggingMiddleware` | Emits one JSON log line per request (method, status, duration, client info) |
| `ResponseLimitingMiddleware` | Truncates tool responses above 500 KB to prevent overflowing LLM context windows |

**Client logging** — Every tool sends real-time progress messages to the MCP client via [`get_optional_context()`](https://gofastmcp.com/servers/context#via-get_context-function), so users see status updates like *"Fetching tasks…"* and *"Found 12 task(s)"* in their client.

### OpenTelemetry Tracing

The server includes native [OpenTelemetry instrumentation](https://gofastmcp.com/servers/telemetry) with zero overhead when unused. To enable trace export:

```bash
# Install the optional OTEL dependency group
uv sync --group otel

# Set the OTLP endpoint and start the server
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
export OTEL_SERVICE_NAME=microsoft-planner-mcp
uv run uvicorn src.server:app --host 0.0.0.0 --port 8000
```

Traces follow the [MCP semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/mcp/) and work with any OTLP-compatible backend (Jaeger, Grafana Tempo, Datadog, New Relic, etc.). The Docker Compose setup includes Jaeger with a UI at `http://localhost:16686`.

### Testing with MCP Inspector

[MCP Inspector](https://github.com/modelcontextprotocol/inspector) lets you test tools interactively in a browser:

1. Start the server:
   ```bash
   uv run uvicorn src.server:app --host 0.0.0.0 --port 8000
   ```

2. In a separate terminal:
   ```bash
   npx @modelcontextprotocol/inspector
   ```

3. Open the URL printed in the terminal (e.g. `http://localhost:6274/?MCP_PROXY_AUTH_TOKEN=...`)

4. Set **Transport Type** to `Streamable HTTP` and **URL** to `http://localhost:8000/mcp`, then click **Connect**.

## Contributing

Contributions are welcome! Please open an issue or submit a pull request. See the [issue templates](.github/ISSUE_TEMPLATE/) for bug reports, feature requests, and tasks.

## License

This project is licensed under the [MIT License](LICENSE).
