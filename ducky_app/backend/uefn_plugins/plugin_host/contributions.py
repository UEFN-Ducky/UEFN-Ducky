"""plugin.json contribution rows: normalizing one row, icons, and stripping a plugin's rows."""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

# ponytail: inline plugin icon assets as data URLs for settings/header; 256KB ceiling.
_ICON_ASSET_MAX_BYTES = 256 * 1024
_ICON_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _automation_contrib_row(row: Any, pid: str) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    nid = str(row.get("id") or row.get("type") or "").strip()
    if not nid:
        return None
    entry = dict(row)
    entry["id"] = nid
    entry["plugin_id"] = pid
    return entry


def _automation_template_row(row: Any, pid: str) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    tid = str(row.get("id") or "").strip()
    if not tid:
        return None
    from backend.automations.store import normalize_graph

    name = str(row.get("label") or row.get("name") or tid).strip() or tid
    req = [str(x).strip() for x in (row.get("requires_plugins") or []) if str(x).strip()]
    # A folder bundle: several workflows in nested folders (templates.bundle_of checks it).
    nested = row.get("bundle") if isinstance(row.get("bundle"), dict) else None
    is_bundle = nested is not None or row.get("kind") == "bundle" or isinstance(row.get("workflows"), list)
    out: dict[str, Any] = {
        "id": tid,
        "label": name,
        "name": name,
        "description": str(row.get("description") or ""),
        "icon": str(row.get("icon") or ("📁" if is_bundle else "⚡")),
        "category": str(row.get("category") or "").strip(),
        "graph": normalize_graph(row.get("graph")),
        "plugin_id": pid,
        "systems": row.get("systems"),
        "requires_plugins": req,
    }
    if is_bundle:
        src = nested or row
        out["bundle"] = {"root": src.get("root") or name, "folders": src.get("folders") or [], "workflows": src.get("workflows") or []}
    return out


def _image_generator_row(row: Any, pid: str) -> dict[str, Any] | None:
    """One Text to Image backend: the plugin's own MCP tool, what it is called and what it costs."""
    if not isinstance(row, dict):
        return None
    gid = str(row.get("id") or "").strip()
    tool = str(row.get("tool") or "").strip()
    if not gid or not tool:
        return None
    try:
        credits = max(0, int(row.get("credits") or 0))
    except (TypeError, ValueError):
        credits = 0
    args = row.get("args") if isinstance(row.get("args"), dict) else {}
    prompt_arg = str(row.get("prompt_arg") or "prompt").strip() or "prompt"
    out: dict[str, Any] = {
        "id": gid,
        "label": str(row.get("label") or gid).strip() or gid,
        "tool": tool,
        "credits": credits,
        "args": dict(args),
        "prompt_arg": prompt_arg,
        "plugin_id": pid,
    }
    # Optional: what it costs in words (shown instead of "~N credits"), whether it spends
    # even at 0 credits (Spend switch + confirm_spend), and its own settings (size, quality…)
    # whose values the node passes into the tool's arguments.
    cost = str(row.get("cost") or "").strip()
    if cost:
        out["cost"] = cost
    if row.get("paid") is True:
        out["paid"] = True
    # Optional: the tool argument a reference picture goes in; such a tool is offered on Edit Image too.
    image_arg = str(row.get("image_arg") or "").strip()
    if image_arg:
        out["image_arg"] = image_arg
    try:
        from backend.automations.catalog import _fields

        fields = [f for f in _fields(row.get("config_fields")) if f["id"] != prompt_arg]
    except Exception:
        fields = []
    if fields:
        out["config_fields"] = fields
    return out


def _media_backend_row(row: Any, pid: str) -> dict[str, Any] | None:
    """One backend for a built-in image / 3D node: the plugin's MCP tool and how the node's
    input pins map onto its arguments (``pins``; ``task_args`` when the wired file came from
    this plugin and carries its task id; ``task_only`` pins accept nothing else)."""
    base = _image_generator_row({**row, "prompt_arg": "__unused__"}, pid) if isinstance(row, dict) else None
    node = str(row.get("node") or "").strip() if isinstance(row, dict) else ""
    if base is None or not node or node == "image.generate":
        return None
    base.pop("prompt_arg", None)

    def names(raw: Any) -> dict[str, str]:
        return {str(k).strip(): str(v).strip() for k, v in raw.items() if str(k).strip() and str(v).strip()} if isinstance(raw, dict) else {}

    out: dict[str, Any] = {**base, "node": node, "pins": names(row.get("pins")), "task_args": names(row.get("task_args")),
                           "cfg": names(row.get("cfg"))}
    only = [str(p).strip() for p in row.get("task_only") or [] if str(p).strip()] if isinstance(row.get("task_only"), list) else []
    if only:
        out["task_only"] = only
    stage = str(row.get("stage") or "").strip()
    if stage:
        out["stage"] = stage
    return out


def _clear_node_handlers(pid: str) -> None:
    try:
        from backend.automations.plugin import clear_for_plugin

        clear_for_plugin(pid)
    except Exception:
        pass


def _strip_contributions_for(pid: str) -> None:
    """Remove contribution rows for one plugin (so a failed register can retry cleanly).

    Workflow node handlers stay: they come from register(), like tools, and are dropped
    only when register() runs again or the runtime goes (invalidate_plugin_runtime). A
    refresh of the rows skips register() for a plugin that is registered or registering,
    so clearing them here left its nodes with no handler while its tools kept working."""
    for key, val in _CONTRIBUTIONS.items():
        if key == "agent_tools":
            if isinstance(val, dict):
                val.pop(pid, None)
            continue
        if not isinstance(val, list):
            continue
        _CONTRIBUTIONS[key] = [
            row
            for row in val
            if not (isinstance(row, dict) and str(row.get("plugin_id") or "") == pid)
        ]


_DEFAULT_ICON_FILES = (
    "assets/icon.svg",
    "assets/icon.png",
    "assets/icon.webp",
    "assets/icon.gif",
    "assets/icon.jpg",
    "assets/icon.jpeg",
)


def _inline_asset_icon(rel: str, root: Path) -> str | None:
    """Turn ``assets/icon.png`` into a data URL, or None if missing/unsafe."""
    from backend.uefn_plugins.webview import sanitize_entry

    entry = sanitize_entry(rel, root)
    if not entry:
        return None
    path = root / entry
    try:
        if not path.is_file() or path.stat().st_size > _ICON_ASSET_MAX_BYTES:
            return None
        mime = _ICON_MIME.get(path.suffix.lower())
        if not mime:
            return None
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return None
    return f"data:{mime};base64,{b64}"


def _plugin_default_icon(root: Path) -> str | None:
    """First colored ``assets/icon.*`` present for this plugin."""
    for rel in _DEFAULT_ICON_FILES:
        url = _inline_asset_icon(rel, root)
        if url:
            return url
    return None


def _resolve_contrib_icon(icon: Any, root: Path) -> str | None:
    """Resolve settings/header icons: asset → data URL; named keys prefer color asset."""
    raw = str(icon or "").strip()
    if raw.startswith("assets/"):
        return _inline_asset_icon(raw, root)
    if raw.startswith("data:image/") or re.match(r"https?://", raw, re.I):
        return raw
    # Named flat keys (user/globe/…) or empty → use plugin color icon when shipped.
    if not raw or re.fullmatch(r"[a-z][a-z0-9_-]*", raw, re.I):
        return _plugin_default_icon(root) or (raw or None)
    return raw  # emoji / short label


# Names from the rest of the host, bound after every definition above so the host
# modules can import each other in any order.
from backend.uefn_plugins.plugin_host.state import _CONTRIBUTIONS  # noqa: E402
