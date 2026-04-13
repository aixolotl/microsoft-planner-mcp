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