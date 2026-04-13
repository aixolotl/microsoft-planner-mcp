from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .auth_provider import auth
from .tools.me import me_router

mcp = FastMCP(
    "Planner MCP",
    auth=auth,
    instructions=(
        "MCP server for Microsoft Planner. "
        "Authenticate with your Microsoft account to read and manage Planner tasks."
    ),
    mask_error_details=True,
)

mcp.mount(me_router)


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    return JSONResponse({"status": "healthy", "service": "planner-mcp"})


# ASGI app — used by uvicorn in production: uvicorn src.server:app
app = mcp.http_app(
    stateless_http=True,
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["mcp-protocol-version", "mcp-session-id", "Authorization", "Content-Type"],
            expose_headers=["mcp-session-id"],
        )
    ],
)

if __name__ == "__main__":
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=8000,
        stateless_http=True,
    )
