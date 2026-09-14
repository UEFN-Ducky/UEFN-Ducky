"""Custom automation templates (AppData JSON). Plugin templates live in plugin.json."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from backend.automations.store import normalize_graph
from frontend.app_paths import resolve_app_data_dir

CUSTOM_PREFIX = "custom:"
_ID_RE = re.compile(r"^custom:[a-z0-9]{8,32}$")


def list_custom() -> list[dict[str, Any]]:
    folder = _dir()
    if not folder.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.json")):
        row = _read(path)
        if row:
            out.append(row)
    return out


def save_custom(
    name: str,
    *,
    description: str = "",
    icon: str = "⚡",
    graph: Any = None,
    template_id: str = "",
) -> dict[str, Any]:
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("Template name is required")
    tid = (template_id or "").strip()
    if tid:
        if not _ID_RE.match(tid):
            raise ValueError("Only custom:… templates can be edited")
    else:
        tid = f"{CUSTOM_PREFIX}{uuid.uuid4().hex[:12]}"
    row = {
        "id": tid,
        "name": cleaned[:64],
        "label": cleaned[:64],
        "description": str(description or "")[:240],
        "icon": (icon or "⚡").strip()[:16] or "⚡",
        "kind": "custom",
        "graph": normalize_graph(graph),
    }
    dest = _dir(for_write=True) / f"{tid.split(':', 1)[-1]}.json"
    dest.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    return row


def delete_custom(template_id: str) -> bool:
    tid = (template_id or "").strip()
    if not _ID_RE.match(tid):
        return False
    path = _dir() / f"{tid.split(':', 1)[-1]}.json"
    if not path.is_file():
        return False
    path.unlink()
    return True


def list_templates() -> list[dict[str, Any]]:
    """Plugin contrib (enabled only) + user custom templates."""
    out: list[dict[str, Any]] = []
    try:
        from backend.uefn_plugins.host import get_ui_contributions
        from backend.uefn_plugins.store import get_enabled_plugin_ids

        enabled = set(get_enabled_plugin_ids())
        contrib = get_ui_contributions()
    except Exception:
        contrib, enabled = {}, set()
    for row in contrib.get("automations_templates") or []:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("plugin_id") or "")
        if pid and pid not in enabled:
            continue
        tid = str(row.get("id") or "").strip()
        if not tid:
            continue
        name = str(row.get("label") or row.get("name") or tid)
        out.append(
            {
                "id": f"plugin:{pid}:{tid}" if pid else tid,
                "name": name,
                "label": name,
                "description": str(row.get("description") or ""),
                "icon": str(row.get("icon") or "⚡"),
                "kind": "plugin",
                "plugin_id": pid,
                "graph": normalize_graph(row.get("graph")),
            }
        )
    out.extend(list_custom())
    return out


def _dir(*, for_write: bool = False) -> Path:
    path = resolve_app_data_dir(for_write=for_write) / "automation_templates"
    if for_write:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    tid = str(data.get("id") or "").strip()
    if not _ID_RE.match(tid):
        return None
    name = str(data.get("name") or data.get("label") or "").strip()
    if not name:
        return None
    return {
        "id": tid,
        "name": name,
        "label": name,
        "description": str(data.get("description") or ""),
        "icon": str(data.get("icon") or "⚡"),
        "kind": "custom",
        "graph": normalize_graph(data.get("graph")),
    }
