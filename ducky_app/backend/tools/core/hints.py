"""MCP tool: UEFN editor Python API notes for execute_python (no example files required)."""

from backend.server import mcp
from backend.tools.core.editor_api_hints import hints_for_topic


@mcp.tool()
def uefn_editor_python_hints(topic: str = "all") -> str:
    """Return UEFN Python API notes for writing correct ``execute_python`` code.

    Call this when you need to script materials, assets, or actors inside the editor and want
    to avoid reflection mistakes (the task is different every time; this is not a fixed example script).

    Args:
        topic: ``all``, ``general``, ``materials``, ``materials_rainbow``, ``actors``, ``save``,
            ``fort_device_scan``, ``pcg_regenerate``.
    """
    return hints_for_topic(topic)
