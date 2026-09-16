"""Local stdio MCP fixture. No app settings, plugin loading, or editor calls."""
from mcp.server.fastmcp import FastMCP

server = FastMCP("ducky-lifecycle-test")


@server.tool()
def echo(value: str) -> str:
    return value


if __name__ == "__main__":
    server.run(transport="stdio")
