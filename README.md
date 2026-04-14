# microsoft-planner-mcp

An MCP server for Microsoft Planner built with [fastmcp](https://github.com/jlowin/fastmcp), authenticated via Microsoft Entra ID (Azure AD) using the On-Behalf-Of flow to call Microsoft Graph.

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
uv run uvicorn src.server:app --host 0.0.0.0 --port 8000

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