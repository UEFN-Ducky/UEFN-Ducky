"""Compact MCP tool index for the system prompt (Cursor-style progressive disclosure)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import hashlib
import json
import re
import threading
from typing import Any

_DESC_MAX = 200
_DESC_MAX_LOCAL = 70
_META_TOOLS = frozenset({"ducky_get_tools", "ducky_call_tool", "ducky_find_tools"})
_BLURB_GROUPS = frozenset({"core", "workspace", "panel", "verse", "testing"})
_CACHE_LOCK = threading.Lock()
# Keep only the latest snapshot for each rendering size.
_INDEX_CACHE: dict[int, tuple[str, str]] = {}


def tool_catalog_row(tool: Any) -> dict[str, Any]:
    """Lossless discovery fields from MCP/host tools; no connection probes."""
    def get(key: str, default: Any = None) -> Any:
        return tool.get(key, default) if isinstance(tool, dict) else getattr(tool, key, default)
    schema = get("inputSchema")
    if schema is None:
        schema = get("parameters", {"type": "object", "properties": {}})
    annotations = get("annotations")
    if hasattr(annotations, "model_dump"):
        annotations = annotations.model_dump(mode="json", exclude_none=True)
    aliases = get("aliases") or []
    return copy.deepcopy({
        "name": str(get("name", "")), "description": str(get("description") or ""),
        "inputSchema": schema, "annotations": annotations,
        "aliases": [a for a in aliases if isinstance(a, str)] if isinstance(aliases, (list, tuple)) else [],
        "provider": get("provider"), "connection_state": get("connection_state") or "unknown",
        "reason": get("reason") or "", "source_revision": get("revision"),
    })


def catalog_revision(tools: list[Any]) -> str:
    """Content revision, including schemas and provider/policy/state metadata."""
    rows = sorted((tool_catalog_row(t) for t in tools), key=lambda r: r["name"])
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def search_tool_catalog(tools: list[Any], query: str = "", *, offset: int = 0,
                        limit: int | None = 50) -> dict[str, Any]:
    """Rank a supplied registry snapshot; unavailable entries remain searchable.

    This is discovery, not an authorization or tool-readiness decision.
    """
    from backend.agent.tools import resolve_invented_tool_name
    rows = [tool_catalog_row(t) for t in tools]
    names = {r["name"] for r in rows}
    query = query.strip()
    resolved = query if query in names else resolve_invented_tool_name(query, names) if query else None
    tokens = set(re.findall(r"[^\W_]+", query.casefold()))
    ranked = []
    for row in rows:
        name = row["name"]
        name_tokens = set(re.findall(r"[^\W_]+", name.casefold()))
        all_tokens = set(re.findall(r"[^\W_]+", (name + " " + row["description"] + " " + " ".join(row["aliases"])).casefold()))
        score = (1000 if query == name else 900 if name == resolved or query in row["aliases"]
                 else 700 if tokens and tokens <= name_tokens
                 else 500 if tokens and tokens <= all_tokens else 0)
        if not query or score:
            ranked.append((score, name, row))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    start = max(0, int(offset))
    size = len(ranked) if limit is None else max(1, min(500, int(limit)))
    matches = [r for _, _, r in ranked[start:start + size]]
    end = start + len(matches)
    return {"revision": catalog_revision(tools), "total": len(ranked), "count": len(matches),
            "offset": start, "next_offset": end if end < len(ranked) else None, "matches": matches}


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
        items = sorted(grouped[group], key=lambda x: x[0])
        lines.append(f"\n### {group}")
        if group not in _BLURB_GROUPS:
            names = [name for name, _ in items]
            lines.append(f"{group}: {len(names)} tools ({', '.join(f'`{n}`' for n in names)})")
            continue
        for name, desc in items:
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
            previous = _INDEX_CACHE.get(limit)
            if previous:
                return "Tool listing failed: cached discovery only; current availability is unknown.\n" + previous[1]
        return ""
    revision = catalog_revision(tools)
    with _CACHE_LOCK:
        hit = _INDEX_CACHE.get(limit)
        if hit and hit[0] == revision:
            return hit[1]
    text = build_tool_index_text(tools, desc_max=limit)
    with _CACHE_LOCK:
        _INDEX_CACHE[limit] = (revision, text)
    return text


def clear_tool_index_cache() -> None:
    with _CACHE_LOCK:
        _INDEX_CACHE.clear()
