"""Host-core MCP tools for multi-language CLI diagnostics (no Store plugin)."""

from __future__ import annotations

from backend.json_util import tool_json
from backend.server import mcp
from backend.tools import code_diagnostics_lib as lib


@mcp.tool()
def code_detect_project(path: str = "", pretty: bool = False) -> str:
    """Detect project kind (node/rust/php/cpp) and which CLI toolchains are on PATH."""
    return tool_json(lib.detect_project(path or None), pretty=pretty)


@mcp.tool()
def code_list_errors(path: str = "", language: str = "", pretty: bool = False) -> str:
    """Run CLI diagnostics; return {path, line, column, message, severity, source}.

    Auto-detects from package.json / Cargo.toml / composer.json / CMakeLists.txt unless
    language is set (typescript|javascript|rust|php|cpp|c). Toolchains must be on PATH.
    Call after workspace_write_file for non-Verse source.
    """
    return tool_json(lib.list_errors(path or None, language or None), pretty=pretty)


@mcp.tool()
def code_open_file(path: str, line: int = 1, column: int = 1, pretty: bool = False) -> str:
    """Open a workspace text file in the Ducky editor at line/column."""
    return tool_json(lib.open_file(path, line=line, column=column), pretty=pretty)
