"""The Duckies Archive as a tree: each group with what was archived with it."""

from __future__ import annotations

from typing import Any


def archive_tree(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Archived chats as a tree: each group with what was archived with it under ``inside``."""
    ids = {r["id"] for r in rows}
    kids: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        parent = r.get("parent_conv_id") or ""
        if parent and parent != r["id"] and parent in ids:
            kids.setdefault(parent, []).append(r)
    seen: set[str] = set()

    def node(r: dict[str, Any]) -> dict[str, Any]:
        seen.add(r["id"])
        inside = [node(k) for k in kids.get(r["id"], []) if k["id"] not in seen]
        count = sum(1 + n["inside_count"] for n in inside)
        return {**r, "inside_count": count, "inside": inside}

    child_ids = {k["id"] for v in kids.values() for k in v}
    out = [node(r) for r in rows if r["id"] not in child_ids]
    # A parent loop (a group archived inside its own ducky) has no root: list it anyway,
    # or the badge counts chats the Archive never shows.
    out += [node(r) for r in rows if r["id"] not in seen]
    return out
