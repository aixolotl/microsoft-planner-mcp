# microsoft-planner-mcp

An MCP server for Microsoft Planner built with [fastmcp](https://github.com/jlowin/fastmcp).

## Setup

**Prerequisites:** Python 3.12+, [uv](https://docs.astral.sh/uv/getting-started/installation/)

```bash
# Clone and install dependencies
git clone https://github.com/raunakburrows/microsoft-planner-mcp
cd microsoft-planner-mcp
uv sync
```

## Running the Server

```bash
uv run python server.py
```

## Running with Docker

```bash
# Build and start
docker compose up
```

**With hot reload** — file changes sync into the container and the server restarts automatically:

```bash
docker compose up --watch
```

How it works:
1. You save a `.py` file locally
2. `compose watch` syncs it into `/app` in the container
3. `watchfiles` detects the change and restarts `python server.py`

## Previewing App UIs Locally

To preview tools that return a visual UI (`app=True`) in your browser without needing a full MCP host:

```bash
uv run fastmcp dev apps server.py
```

This opens a browser-based preview showing all app tools (those decorated with `app=True`).

## Testing with MCP Inspector

To test all tools (including non-UI tools like `health`) interactively in a browser:

1. Start the server:
   ```bash
   uv run server.py
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