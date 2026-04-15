# Microsoft Planner MCP

An MCP server for Microsoft Planner built with [FastMCP](https://gofastmcp.com), authenticated via Microsoft Entra ID (Azure AD) using the On-Behalf-Of (OBO) flow to call Microsoft Graph.

## FastMCP Features

This server uses the following [FastMCP](https://gofastmcp.com) features in production.

### Tools

Every capability is exposed as an [MCP tool](https://gofastmcp.com/servers/tools) — callable functions that an LLM agent invokes by name with typed arguments. Tools are grouped into domain routers and mounted on the main server.

| Tool | Description |
|---|---|
| `get_me` | Fetch the authenticated user's Graph profile |
| `list_my_plans` | List all Planner plans the user is a member of |
| `list_group_plans` | List plans belonging to a specific Microsoft 365 group |
| `list_buckets` | List buckets within a plan |
| `list_my_tasks` | List tasks assigned to the authenticated user |
| `list_tasks` | List all tasks in a plan |
| `get_task_details` | Get checklist, description, and references for a task |
| `create_task` | Create a new task in a plan |
| `update_task` | Update task title, bucket, due date, assignments, or completion |
| `update_task_details` | Update task description, checklist items, and external references |
| `delete_task` | Delete a task by ID and ETag |

Read-only tools carry `readOnlyHint: true` [annotations](https://gofastmcp.com/servers/tools#tool-annotations) so clients can signal to the LLM that they don't modify state.

### Server Composition

Tools are split into four domain routers (`me`, `plans`, `tasks`, `buckets`) each created as a standalone `FastMCP` instance and [mounted](https://gofastmcp.com/servers/composition) on the main app:

```python
mcp.mount(me_router)
mcp.mount(plans_router)
mcp.mount(tasks_router)
mcp.mount(buckets_router)
```

This keeps each domain's tools, imports, and tests isolated.

### Authentication — Azure OAuthProxy

The server uses FastMCP's [`OAuthProxy`](https://gofastmcp.com/servers/auth/oauth-proxy) pattern via a custom `AzureProvider`. Azure Entra ID does not support Dynamic Client Registration (DCR), so the provider acts as a DCR-compliant proxy facing MCP clients while using the pre-registered app credentials with Azure.

When a tool call arrives the server exchanges the MCP session token for a Microsoft Graph token via the [On-Behalf-Of flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow), scoped to `Tasks.ReadWrite` and `User.Read`.

Passing `mask_error_details=True` to the `FastMCP` constructor ensures that internal exceptions (stack traces, Graph API URLs) are never surfaced to clients.

### Middleware

Five [built-in middleware](https://gofastmcp.com/servers/middleware#built-in-middleware) layers are stacked on the server (outermost first):

| Middleware | Purpose | Docs |
|---|---|---|
| `ErrorHandlingMiddleware` | Catches all unhandled exceptions, logs the full stack trace server-side, and converts them to clean MCP errors | [↗](https://gofastmcp.com/servers/middleware#error-handling) |
| `SlidingWindowRateLimitingMiddleware` | Enforces 60 req/min ceiling per client to protect the Microsoft Graph quota (10k req/10min per tenant) | [↗](https://gofastmcp.com/servers/middleware#rate-limiting) |
| `TimingMiddleware` | Records wall-clock duration for every MCP operation — identifies slow Graph calls | [↗](https://gofastmcp.com/servers/middleware#timing) |
| `StructuredLoggingMiddleware` | Emits one JSON log line per request including method, status, duration, and client info — queryable in Datadog, CloudWatch, etc. | [↗](https://gofastmcp.com/servers/middleware#logging) |
| `ResponseLimitingMiddleware` | Truncates tool responses above 500 KB — prevents large Graph list payloads from overflowing an LLM's context window | [↗](https://gofastmcp.com/servers/middleware#response-limiting) |

### Client Logging

Every read tool accepts an optional [`ctx: Context`](https://gofastmcp.com/servers/logging) parameter and sends real-time progress messages to the MCP client:

```python
@plans_router.tool(name="list_my_plans")
async def list_my_plans(..., ctx: Context | None = None):
    if ctx:
        await ctx.info("Fetching plans from Microsoft Graph...")
    plans = await service.list_my_plans(...)
    if ctx:
        await ctx.info(f"Found {len(plans)} plan(s).")
    return plans
```

`client_log_level="info"` is set on the `FastMCP` constructor so `ctx.info()` messages are forwarded to clients. Without it, they are suppressed.

### OpenTelemetry Tracing

FastMCP includes [native OTEL instrumentation](https://gofastmcp.com/servers/telemetry) — zero overhead when unused (no-op API without the SDK). The server activates SDK export programmatically in `src/telemetry.py` when `OTEL_EXPORTER_OTLP_ENDPOINT` is set in the environment.

The OTEL SDK packages are an optional dependency group:

```bash
# Install only when you want traces exported
uv sync --group otel
```

Exported spans follow the [MCP semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/mcp/) (`tools/call {name}`, `rpc.system = "mcp"`) and carry auth attributes (`enduser.id`, `enduser.scope`) on each tool call.

Works with any OTLP-compatible backend: Jaeger, Grafana Tempo, Datadog, New Relic, etc.

```bash
# Example: export to a local Jaeger instance
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
export OTEL_SERVICE_NAME=microsoft-planner-mcp
uv run uvicorn src.server:app --host 0.0.0.0 --port 8000
```

### Custom Routes

The server registers a [`/health`](https://gofastmcp.com/servers/server#custom-routes) endpoint alongside the MCP endpoint using `@mcp.custom_route`:

```
GET http://localhost:8000/health  →  { "status": "ok" }
GET http://localhost:8000/mcp     →  MCP Streamable HTTP transport
```

## Azure App Registration

1. Go to **Azure Portal → Microsoft Entra ID → App registrations → New registration**
2. Set the **Redirect URI** to `Web` → `http://localhost:8000/auth/callback`
3. Under **Expose an API**:
   - Set the Application ID URI (default: `api://<client-id>`)
   - Add a scope named **`mcp-access`** (admin consent required)
4. Under **Manifest**, set `"requestedAccessTokenVersion": 2`
5. Under **API permissions → Add a permission → Microsoft Graph → Delegated**:
   - Add `Tasks.ReadWrite` and `User.Read`
   - Click **Grant admin consent**
6. Under **Certificates & secrets**, create a client secret and copy the value
7. Note your **Application (client) ID** and **Directory (tenant) ID**

## Setup

**Prerequisites:** Python 3.12+, [uv](https://docs.astral.sh/uv/getting-started/installation/)

```bash
# Clone and install dependencies (including dev tools)
git clone https://github.com/raunakburrows/microsoft-planner-mcp
cd microsoft-planner-mcp
uv sync --dev

# Configure environment
cp .env.example .env
# Edit .env and fill in CLIENT_ID, CLIENT_SECRET, TENANT_ID
```

## Running the Server

```bash
# Development
uv run uvicorn src.server:app --host 0.0.0.0 --port 8000 --reload

# Or with the __main__ guard
uv run python src/server.py
```

Server is available at `http://localhost:8000/mcp`. Health check at `http://localhost:8000/health`.

## Running with Docker

```bash
# Build and start
docker compose up
```

## Running Tests

Requires dev dependencies (`uv sync --dev` in setup above).

```bash
uv run pytest
```

Run with verbose output:

```bash
uv run pytest -v
```

## Testing with MCP Inspector

To test all tools (including non-UI tools like `health`) interactively in a browser:

1. Start the server:
   ```bash
   uv run python src/server.py
   ```
   Or with Docker:
   ```bash
   docker compose up -d
   ```

2. In a separate terminal, launch MCP Inspector:
   ```bash
   npx @modelcontextprotocol/inspector
   ```

3. Open the URL printed in the terminal (e.g. `http://localhost:6274/?MCP_PROXY_AUTH_TOKEN=...`)

4. Set **Transport Type** to `Streamable HTTP` and **URL** to `http://localhost:8000/mcp`, then click **Connect**.