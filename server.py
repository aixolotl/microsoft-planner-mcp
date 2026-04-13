from fastmcp import FastMCP

mcp = FastMCP("My MCP Server")

@mcp.tool
def health() -> dict:
    """Returns health and status information about the MCP server."""
    import datetime
    return {
        "status": "ok",
        "server": "My MCP Server",
        "version": "1.0.0",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)