"""Which plugin panels are open and on screen, for the agents that test them.

A plugin's panel code (its UI, its polling, a panel-side test) only runs while its tab
is the visible one. An agent asked such a panel to run a check while the person was on
another tab, read the old result back, and spent an hour on a "data consistency" theory.
The window reports each panel tab as it is shown, hidden or closed; agents' tools run in
another process, so the state lives in the shared database (``workspace_state``).
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

_TABLE = "workspace_state"
_PREFIX = "plugin_panel:"
_NOTED: set[tuple[str, str, float]] = set()
_NOTED_LOCK = threading.Lock()


def _key(plugin_id: str) -> str:
    return f"{_PREFIX}{plugin_id}"


def _parse_tab(tab_id: str) -> tuple[str, str]:
    """``plugin:<plugin_id>:<panel_id>`` (an editor tab id) → (plugin_id, panel_id)."""
    parts = str(tab_id or "").split(":")
    if len(parts) < 3 or parts[0] != "plugin":
        return "", ""
    return parts[1].strip().lower(), parts[2].split("~", 1)[0].strip()


def tab_plugin(tab_id: str) -> str:
    """The plugin a panel tab belongs to ('' for any other tab)."""
    return _parse_tab(tab_id)[0]


def note_panel(window_id: str, tab_id: str, *, visible: bool, open_: bool = True) -> None:
    """The window says one plugin panel tab was shown, hidden or closed."""
    plugin_id, panel_id = _parse_tab(tab_id)
    if not plugin_id or not panel_id:
        return
    from backend.store.repos import kv

    doc = kv.get_doc(_TABLE, _key(plugin_id))
    tabs = dict(doc.get("tabs") or {}) if isinstance(doc, dict) else {}
    closed = float(doc.get("closed") or 0.0) if isinstance(doc, dict) else 0.0
    slot = f"{window_id or 'main'}|{tab_id}"
    if open_:
        tabs[slot] = {"panel": panel_id, "visible": bool(visible), "at": time.time()}
    elif tabs.pop(slot, None) is not None:
        closed = time.time()
    kv.set_doc(_TABLE, _key(plugin_id), {"tabs": tabs, "closed": closed})


def forget_all() -> None:
    """At app start: no panel is open yet (a crash may have left old rows)."""
    from backend.store.repos import kv

    for key in kv.list_docs(_TABLE, _PREFIX):
        kv.delete_doc(_TABLE, key)


def panel_state(plugin_id: str) -> dict[str, Any]:
    """{open, visible, since, closed}: ``since`` is when the last panel tab changed (0 = never
    opened), ``closed`` when a tab of it last closed (0 = none since the app started)."""
    try:
        from backend.store.repos import kv

        doc = kv.get_doc(_TABLE, _key(str(plugin_id or "").strip().lower()))
    except Exception:
        doc = None
    tabs = (doc or {}).get("tabs") if isinstance(doc, dict) else None
    rows = [row for row in (tabs or {}).values() if isinstance(row, dict)]
    return {
        "open": bool(rows),
        "visible": any(row.get("visible") for row in rows),
        "since": max((float(row.get("at") or 0) for row in rows), default=0.0),
        "closed": float((doc or {}).get("closed") or 0.0) if isinstance(doc, dict) else 0.0,
        "panels":sorted({str(row.get("panel") or "") for row in rows if row.get("panel")}),
    }


def _has_panels(plugin_id: str) -> list[str]:
    try:
        from backend.uefn_plugins.host import get_contributions

        rows = get_contributions().get("ui_panels") or []
    except Exception:
        return []
    return [str(r.get("id") or "") for r in rows if isinstance(r, dict) and r.get("plugin_id") == plugin_id]


def _label(plugin_id: str) -> str:
    try:
        from backend.uefn_plugins.store import load_plugin_manifest

        return str((load_plugin_manifest(plugin_id) or {}).get("label") or plugin_id)
    except Exception:
        return plugin_id


def panel_note_once(plugin_id: str) -> str:
    """What an agent's tool call says the first time it uses a plugin whose panels are all
    off screen ('' otherwise): once per chat until a panel is shown and hidden again."""
    panels = _has_panels(plugin_id)
    if not panels:
        return ""
    try:
        from backend.workspace.identity import resolve_context

        ctx = resolve_context()
    except Exception:
        ctx = None
    chat = str(getattr(ctx, "conv_id", "") or "") if ctx is not None else ""
    if not chat:
        return ""  # a person is looking at the app; this is for agents
    state = panel_state(plugin_id)
    if state["visible"]:
        return ""
    marker = (plugin_id, chat, state["since"])
    with _NOTED_LOCK:
        if marker in _NOTED:
            return ""
        _NOTED.add(marker)
    where = "is open on a tab that is not showing" if state["open"] else "is not open"
    return (
        f"The {_label(plugin_id)} panel {where}, so its panel code (UI, polling, panel-side tests) "
        "is paused until the person has it on screen; backend tools work as usual. Only if you "
        f'need the panel itself, ducky_ui_navigate("plugin_panel", "{plugin_id}:{panels[0]}") '
        "puts an Open button for it in the chat (never pulls it to the front)."
    )


_LOCAL_NAME = re.compile(r"[^A-Za-z0-9_-]+")


def plugin_local_dir(plugin_id: str, name: str = "") -> Path:
    """``<AppData>/plugin_local/<plugin>/<name>``: a plugin's rebuildable files on this PC."""
    from backend.skills.store import appdata_dir

    pid = _LOCAL_NAME.sub("", str(plugin_id or "").strip().lower())
    if not pid:
        raise ValueError("plugin id required")
    sub = _LOCAL_NAME.sub("_", str(name or "").strip()).strip("_")
    folder = appdata_dir() / "plugin_local" / pid
    if sub:
        folder = folder / sub
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def erase_plugin_local(plugin_id: str) -> None:
    import shutil

    from backend.skills.store import appdata_dir

    pid = _LOCAL_NAME.sub("", str(plugin_id or "").strip().lower())
    if pid:
        shutil.rmtree(appdata_dir() / "plugin_local" / pid, ignore_errors=True)
