"""Compact MCP tool index for the system prompt (Cursor-style progressive disclosure)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import re
import threading
from typing import Any

_DESC_MAX = 200
_DESC_MAX_LOCAL = 70
_META_TOOLS = frozenset({"ducky_get_tools", "ducky_call_tool", "ducky_find_tools"})
_CACHE_LOCK = threading.Lock()
# key: (desc_max, tool_name_tuple) → text
_INDEX_CACHE: dict[tuple[int, tuple[str, ...]], str] = {}


def truncate_desc(text: str, limit: int = _DESC_MAX) -> str:
    one = re.sub(r"\s+", " ", (text or "").strip())
    if len(one) <= limit:
        return one
    return one[: max(0, limit - 16)].rstrip() + "... [truncated]"


def tool_group(name: str) -> str:
    n = name or ""
    if "__" in n:
        return f"nested:{n.split('__', 1)[0]}"
    if n.startswith("blender_") or n.startswith("discord_"):
        return "desktop"
    if n.startswith("workspace_") or n.startswith("code_"):
        return "workspace"
    if n.startswith("ducky_"):
        return "panel"
    if "verse" in n or n.startswith("list_verse") or n.startswith("search_verse") or n.startswith("get_verse"):
        return "verse"
    if n.startswith("tester_") or n.startswith("verse_test") or n.startswith("device_graph"):
        return "testing"
    return "core"


def build_tool_index_text(
    tools: list[Any],
    *,
    exclude: frozenset[str] | None = None,
    desc_max: int = _DESC_MAX,
) -> str:
    """Name + short blurb catalog. Full schemas stay out — use ducky_get_tools."""
    skip = exclude or _META_TOOLS
    limit = max(0, int(desc_max))
    grouped: dict[str, list[tuple[str, str]]] = {}
    for t in tools:
        name = str(getattr(t, "name", "") or "").strip()
        if not name or name in skip:
            continue
        desc = truncate_desc(str(getattr(t, "description", "") or ""), limit) if limit > 0 else ""
        grouped.setdefault(tool_group(name), []).append((name, desc))

    lines = [
        "## Tool index (lazy — schemas via ducky_get_tools)",
        "Full JSON schemas are NOT in this prompt. Call `ducky_get_tools(name=…)` or "
        "`ducky_get_tools(pattern=…)` then `ducky_call_tool(name, arguments)` for non-floor tools. "
        "Floor tools (workspace_*, ducky_get_status, ducky_ask_user, get/call) are always in tools[].",
    ]
    for group in sorted(grouped):
        lines.append(f"\n### {group}")
        for name, desc in sorted(grouped[group], key=lambda x: x[0]):
            if desc:
                lines.append(f"- `{name}` — {desc}")
            else:
                lines.append(f"- `{name}`")
    return "\n".join(lines).rstrip() + "\n"


def _list_tools_blocking() -> list[Any]:
    from backend.agent.tools import list_mcp_tools

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(list_mcp_tools())

    # Already inside an event loop (agent runner) — use a worker thread.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(list_mcp_tools())).result(timeout=60)


def tool_index_prompt_block_sync(*, desc_max: int = _DESC_MAX) -> str:
    """Cached compact index for sync prompt builders (hot path / async-safe)."""
    limit = max(0, int(desc_max))
    try:
        tools = _list_tools_blocking()
    except Exception:
        with _CACHE_LOCK:
            for key, text in _INDEX_CACHE.items():
                if key[0] == limit and text:
                    return text
        return ""
    names = tuple(sorted(str(getattr(t, "name", "") or "") for t in tools))
    cache_key = (limit, names)
    with _CACHE_LOCK:
        hit = _INDEX_CACHE.get(cache_key)
        if hit:
            return hit
    text = build_tool_index_text(tools, desc_max=limit)
    with _CACHE_LOCK:
        _INDEX_CACHE[cache_key] = text
    return text


def clear_tool_index_cache() -> None:
    with _CACHE_LOCK:
        _INDEX_CACHE.clear()
